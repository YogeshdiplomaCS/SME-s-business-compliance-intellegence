"""Database layer for the SME Compliance & Document Intelligence Platform (MySQL).

* Uses a thread-safe connection pool (Streamlit runs every session in its own thread).
* Creates the database and tables automatically from database.sql on first use.
* Passwords are stored with bcrypt; old unsalted SHA-256 hashes are still accepted and upgraded at login.
"""
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error, pooling

try:
    import bcrypt
except ImportError:  # pragma: no cover
    bcrypt = None

load_dotenv()
log = logging.getLogger("database")
SCHEMA_FILE = Path(__file__).with_name("database.sql")

_DOC_FIELDS = {"document_name", "document_type", "file_path", "file_extension", "category",
               "extracted_text", "metadata", "status"}


class Database:
    def __init__(self):
        self.host = os.getenv("DATABASE_HOST", "localhost")
        self.port = int(os.getenv("DATABASE_PORT", "3306") or 3306)
        self.user = os.getenv("DATABASE_USER", "root")
        self.password = os.getenv("DATABASE_PASSWORD", "")
        self.database = os.getenv("DATABASE_NAME", "sme_compliance_db")
        self.last_error = None
        self._pool = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ setup
    def _conn_args(self, with_database=True):
        args = dict(host=self.host, port=self.port, user=self.user, password=self.password,
                    charset="utf8mb4", collation="utf8mb4_unicode_ci", connection_timeout=10)
        if with_database:
            args["database"] = self.database
        return args

    @staticmethod
    def _statements():
        if not SCHEMA_FILE.exists():
            raise FileNotFoundError(f"{SCHEMA_FILE.name} not found next to database.py")
        lines = [ln for ln in SCHEMA_FILE.read_text(encoding="utf-8").splitlines()
                 if not ln.strip().startswith("--")]
        statements = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
        return [s for s in statements if not re.match(r"(?i)(CREATE\s+DATABASE|USE)\b", s)]

    def _ensure_schema(self):
        if not re.fullmatch(r"[A-Za-z0-9_]+", self.database):
            raise Error(f"Invalid DATABASE_NAME: {self.database!r}")
        try:  # create the database when the account is allowed to; otherwise it must already exist
            conn = mysql.connector.connect(**self._conn_args(with_database=False))
            cur = conn.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            conn.commit()
            cur.close()
            conn.close()
        except Error as exc:
            log.warning("Could not create database (it may already exist): %s", exc)
        conn = mysql.connector.connect(**self._conn_args())
        try:
            cur = conn.cursor()
            for statement in self._statements():
                cur.execute(statement)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def _get_pool(self):
        with self._lock:
            if self._pool is None:
                self._ensure_schema()
                self._pool = pooling.MySQLConnectionPool(
                    pool_name="sme_pool", pool_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
                    pool_reset_session=True, **self._conn_args())
            return self._pool

    def connect(self):
        """Return True when the database can be reached (used for diagnostics)."""
        try:
            conn = self._get_pool().get_connection()
            conn.close()
            return True
        except (Error, OSError, FileNotFoundError) as exc:
            self.last_error = str(exc)
            return False

    # -------------------------------------------------------------- primitives
    def _execute(self, sql, params=(), fetch=None):
        """fetch: None (write), 'one' or 'all'. Returns data / {'lastrowid','rowcount'}; None on error."""
        conn = cur = None
        try:
            conn = self._get_pool().get_connection()
            cur = conn.cursor(dictionary=True)
            cur.execute(sql, params or ())
            if fetch == "one":
                return cur.fetchone() or {}
            if fetch == "all":
                return cur.fetchall()
            conn.commit()
            return {"lastrowid": cur.lastrowid, "rowcount": cur.rowcount}
        except (Error, OSError, FileNotFoundError) as exc:
            self.last_error = str(exc)
            log.warning("SQL failed: %s", exc)
            if conn is not None:
                try:
                    conn.rollback()
                except Error:
                    pass
            return None
        finally:
            if cur is not None:
                cur.close()
            if conn is not None:
                conn.close()

    def execute_query(self, query, params=None, commit=True):
        """Run a write statement; returns the new row id (0 for UPDATE/DELETE) or None on error."""
        result = self._execute(query, params)
        return None if result is None else result["lastrowid"]

    def fetch_one(self, query, params=None):
        """Row dict, None when there is no row (or on error - check last_error)."""
        row = self._execute(query, params, fetch="one")
        return row or None

    def fetch_all(self, query, params=None):
        """List of row dicts; None on error."""
        return self._execute(query, params, fetch="all")

    # --------------------------------------------------------------- passwords
    @staticmethod
    def hash_password(password):
        if bcrypt:
            return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode()
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
        return f"pbkdf2${salt}${digest}"

    @staticmethod
    def verify_password(password, hashed):
        hashed = hashed or ""
        if hashed.startswith("$2"):
            if not bcrypt:
                return False
            try:
                return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode())
            except ValueError:
                return False
        if hashed.startswith("pbkdf2$"):
            try:
                _, salt, digest = hashed.split("$")
            except ValueError:
                return False
            check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
            return hmac.compare_digest(check, digest)
        # legacy: unsalted SHA-256 from the first version of the app
        return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), hashed)

    @staticmethod
    def _needs_rehash(hashed):
        return not (hashed or "").startswith("$2" if bcrypt else "pbkdf2$")

    # ------------------------------------------------------------------- users
    def username_exists(self, username):
        return self.fetch_one("SELECT id FROM users WHERE username = %s", (username,)) is not None

    user_exists = username_exists

    def email_exists(self, email, exclude_user_id=None):
        if exclude_user_id:
            row = self.fetch_one("SELECT id FROM users WHERE email = %s AND id != %s", (email, exclude_user_id))
        else:
            row = self.fetch_one("SELECT id FROM users WHERE email = %s", (email,))
        return row is not None

    def register_user(self, full_name, email, mobile, username, password, language="en"):
        """Create a user. Returns the new user id, or None (see last_error)."""
        sql = ("INSERT INTO users (username, email, mobile, password, full_name, language_preference) "
               "VALUES (%s, %s, %s, %s, %s, %s)")
        return self.execute_query(sql, (username, email, mobile, self.hash_password(password), full_name, language)) or None

    def get_user_by_username(self, username):
        return self.fetch_one("SELECT * FROM users WHERE username = %s", (username,))

    def get_user_by_email(self, email):
        return self.fetch_one("SELECT * FROM users WHERE email = %s", (email,))

    def get_user_by_id(self, user_id):
        return self.fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))

    def login_user(self, username, password):
        """Return the user row for valid credentials (username or e-mail), else None."""
        self.last_error = None
        username = (username or "").strip()
        user = self.get_user_by_email(username) if "@" in username else self.get_user_by_username(username)
        if not user or not user.get("is_active", True):
            return None
        if not self.verify_password(password, user["password"]):
            return None
        if self._needs_rehash(user["password"]):
            self.execute_query("UPDATE users SET password = %s WHERE id = %s",
                               (self.hash_password(password), user["id"]))
        return user

    def update_profile(self, user_id, full_name, email, mobile):
        return self.execute_query("UPDATE users SET full_name = %s, email = %s, mobile = %s WHERE id = %s",
                                  (full_name, email, mobile, user_id))

    def update_language_preference(self, user_id, language):
        return self.execute_query("UPDATE users SET language_preference = %s WHERE id = %s", (language, user_id))

    update_language = update_language_preference

    # ------------------------------------------------------------ email tokens
    def create_email_token(self, user_id, token, expires_in=1800):
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        return self.execute_query("INSERT INTO email_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)",
                                  (user_id, token, expires_at))

    def get_email_token(self, token):
        return self.fetch_one("SELECT * FROM email_tokens WHERE token = %s AND is_used = FALSE AND expires_at > NOW()",
                              (token,))

    def use_email_token(self, token):
        return self.execute_query("UPDATE email_tokens SET is_used = TRUE WHERE token = %s", (token,))

    def cleanup_expired_tokens(self):
        return self.execute_query("DELETE FROM email_tokens WHERE expires_at < NOW()")

    # --------------------------------------------------------------- documents
    def save_document(self, user_id, document_name, document_type, file_path, file_extension):
        """Insert a document row (status 'pending'). Returns the new id or None."""
        sql = ("INSERT INTO documents (user_id, document_name, document_type, file_path, file_extension, status) "
               "VALUES (%s, %s, %s, %s, %s, 'pending')")
        return self.execute_query(sql, (user_id, document_name[:255], (document_type or "")[:50] or "unknown",
                                        file_path[:500], (file_extension or "")[:10])) or None

    upload_document = save_document

    def get_user_documents(self, user_id, status=None, category=None, search_term=None):
        sql = ("SELECT id, user_id, document_name, document_type, file_path, file_extension, uploaded_at, "
               "category, metadata, status FROM documents WHERE user_id = %s")
        params = [user_id]
        if status:
            sql += " AND status = %s"
            params.append(status)
        if category:
            sql += " AND category = %s"
            params.append(category)
        if search_term:
            sql += " AND (document_name LIKE %s OR extracted_text LIKE %s)"
            params += [f"%{search_term}%"] * 2
        return self.fetch_all(sql + " ORDER BY uploaded_at DESC, id DESC", tuple(params))

    def get_document_by_id(self, document_id, user_id=None):
        if user_id is not None:
            return self.fetch_one("SELECT * FROM documents WHERE id = %s AND user_id = %s", (document_id, user_id))
        return self.fetch_one("SELECT * FROM documents WHERE id = %s", (document_id,))

    def update_document(self, document_id, **fields):
        """Update any of: document_name, category, extracted_text, metadata, status ..."""
        fields = {k: v for k, v in fields.items() if k in _DOC_FIELDS}
        if not fields:
            return None
        assignments = ", ".join(f"{k} = %s" for k in fields)
        return self.execute_query(f"UPDATE documents SET {assignments} WHERE id = %s",
                                  tuple(fields.values()) + (document_id,))

    def update_document_status(self, document_id, status):
        return self.update_document(document_id, status=status)

    def delete_document(self, document_id, user_id=None):
        """Delete a document. Returns the deleted row (so the caller can remove the file) or None."""
        doc = self.get_document_by_id(document_id, user_id)
        if not doc:
            return None
        result = self._execute("DELETE FROM documents WHERE id = %s", (document_id,))
        return doc if result and result["rowcount"] else None

    def get_all_documents_for_search(self, user_id):
        return self.fetch_all(
            "SELECT id, document_name, category, extracted_text FROM documents "
            "WHERE user_id = %s AND status = 'completed' AND extracted_text IS NOT NULL "
            "ORDER BY uploaded_at DESC LIMIT 100", (user_id,))

    def clear_analysis(self, document_id):
        """Remove old dates / extracted fields before an analysis is stored again."""
        self.execute_query("DELETE FROM important_dates WHERE document_id = %s", (document_id,))
        self.execute_query("DELETE FROM extracted_info WHERE document_id = %s", (document_id,))

    # --------------------------------------------------------- extracted info
    def save_extracted_info(self, document_id, field_name, field_value, confidence_score=None):
        return self.execute_query(
            "INSERT INTO extracted_info (document_id, field_name, field_value, confidence_score) "
            "VALUES (%s, %s, %s, %s)",
            (document_id, str(field_name)[:100], str(field_value)[:2000], confidence_score))

    def get_extracted_info(self, document_id):
        return self.fetch_all("SELECT * FROM extracted_info WHERE document_id = %s ORDER BY id", (document_id,))

    # ---------------------------------------------------------- important dates
    def save_important_date(self, document_id, date_type, date_value, description=None):
        return self.execute_query(
            "INSERT INTO important_dates (document_id, date_type, date_value, description) VALUES (%s, %s, %s, %s)",
            (document_id, str(date_type)[:50], date_value, (description or "")[:255]))

    def get_document_dates(self, document_id):
        return self.fetch_all("SELECT * FROM important_dates WHERE document_id = %s ORDER BY date_value",
                              (document_id,))

    def get_user_important_dates(self, user_id):
        """All dates of a user's documents, with the document name (used for alerts)."""
        return self.fetch_all(
            "SELECT idt.id, idt.document_id, doc.document_name, idt.date_type, idt.date_value, "
            "idt.description, idt.alert_sent FROM important_dates idt "
            "JOIN documents doc ON doc.id = idt.document_id WHERE doc.user_id = %s "
            "ORDER BY idt.date_value", (user_id,))

    def get_important_dates(self, document_id=None, user_id=None):
        if document_id:
            return self.get_document_dates(document_id)
        if user_id:
            return self.get_user_important_dates(user_id)
        return []

    def get_pending_alerts(self, days=30):
        """Unsent deadline rows (all users) due within `days` days or already overdue. None on DB error."""
        return self.fetch_all(
            "SELECT idt.id, idt.document_id, doc.document_name, idt.date_type, idt.date_value, idt.description, "
            "u.id AS user_id, u.email, u.full_name FROM important_dates idt "
            "JOIN documents doc ON doc.id = idt.document_id JOIN users u ON u.id = doc.user_id "
            "WHERE idt.alert_sent = FALSE AND u.is_active = TRUE "
            "AND idt.date_value <= DATE_ADD(CURDATE(), INTERVAL %s DAY) ORDER BY u.id, idt.date_value",
            (int(days),))

    def mark_alert_sent(self, date_id):
        return self.execute_query("UPDATE important_dates SET alert_sent = TRUE WHERE id = %s", (date_id,))

    def reset_alert_flags(self):
        return self.execute_query("UPDATE important_dates SET alert_sent = FALSE")

    # ---------------------------------------------------------- search history
    def save_search_history(self, user_id, query, search_type="text", results_count=0):
        return self.execute_query(
            "INSERT INTO search_history (user_id, query, search_type, results_count) VALUES (%s, %s, %s, %s)",
            (user_id, query, search_type, results_count))

    def get_search_history(self, user_id, limit=50):
        return self.fetch_all("SELECT * FROM search_history WHERE user_id = %s ORDER BY searched_at DESC, id DESC LIMIT %s",
                              (user_id, int(limit)))

    # -------------------------------------------------------------- statistics
    def get_user_stats(self, user_id):
        """Dashboard numbers; None on database error."""
        row = self.fetch_one(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(status = 'completed'), 0) AS completed, "
            "COALESCE(SUM(status IN ('pending', 'processing')), 0) AS pending, "
            "COALESCE(SUM(status = 'failed'), 0) AS failed, "
            "COALESCE(SUM(category = 'invoice'), 0) AS invoices, "
            "COALESCE(SUM(category = 'contract'), 0) AS contracts, "
            "COALESCE(SUM(category = 'license'), 0) AS licenses "
            "FROM documents WHERE user_id = %s", (user_id,))
        if row is None:
            return None
        return {"total_documents": int(row["total"]), "completed_documents": int(row["completed"]),
                "pending_documents": int(row["pending"]), "failed_documents": int(row["failed"]),
                "invoices": int(row["invoices"]), "contracts": int(row["contracts"]),
                "licenses": int(row["licenses"])}

    def get_category_counts(self, user_id):
        rows = self.fetch_all("SELECT category, COUNT(*) AS n FROM documents "
                              "WHERE user_id = %s AND category IS NOT NULL GROUP BY category ORDER BY n DESC",
                              (user_id,))
        return {r["category"]: int(r["n"]) for r in rows or []}
