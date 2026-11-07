"""SMTP e-mail handling for SME Document Intelligence.

Configuration (environment / .env):
    SMTP_HOST       e.g. smtp.gmail.com
    SMTP_PORT       587 (STARTTLS) or 465 (SSL)
    SMTP_SECURITY   starttls | ssl | none   (default chosen from the port)
    SMTP_USER       login name (usually the full e-mail address)
    SMTP_PASSWORD   password; for Gmail this must be an App Password
    SMTP_FROM       sender address (defaults to SMTP_USER)
    SMTP_FROM_NAME  display name (default: SME Document Intelligence)
    SMTP_TIMEOUT    seconds (default 20)
"""
import logging
import os
import re
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from html import escape

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("email_handler")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _env(*names, default=""):
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def format_date(dt):
    """Format date for display"""
    if dt is None:
        return "N/A"
    if isinstance(dt, str):
        return dt
    return dt.strftime("%Y-%m-%d")


LEVEL_LABELS = {
    "expired": "Expired",
    "critical": "Critical",
    "warning": "Warning",
    "upcoming": "Upcoming"
}


class EmailHandler:
    def __init__(self):
        self._load()

    # ------------------------------------------------------------------ config
    def _load(self):
        load_dotenv(override=True)
        self.host = _env("SMTP_HOST", "SMTP_SERVER", "EMAIL_HOST", default="smtp.gmail.com")
        try:
            self.port = int(_env("SMTP_PORT", "EMAIL_PORT", default="587"))
        except ValueError:
            self.port = 0
        self.user = _env("SMTP_USER", "SMTP_USERNAME", "EMAIL_ADDRESS", "EMAIL_USER")
        password = _env("SMTP_PASSWORD", "EMAIL_PASSWORD", "EMAIL_PASS")
        if "gmail" in self.host.lower():
            password = password.replace(" ", "")  # Google shows app passwords in groups
        self.password = password
        # SMTP_FROM may be written as  Name <address@example.com>  -- keep only the address
        raw_from = _env("SMTP_FROM", "EMAIL_FROM", default=self.user)
        self.from_addr = parseaddr(raw_from)[1] or raw_from
        self.from_name = _env("SMTP_FROM_NAME", default="SME Document Intelligence")
        default_sec = "ssl" if self.port == 465 else "starttls"
        security = _env("SMTP_SECURITY", "SMTP_ENCRYPTION", default=default_sec).lower()
        aliases = {"tls": "starttls", "starttls": "starttls", "ssl": "ssl", "smtps": "ssl", "ssl/tls": "ssl",
                   "none": "none", "false": "none", "no": "none", "off": "none", "0": "none"}
        self.security = aliases.get(security, security)
        try:
            self.timeout = float(_env("SMTP_TIMEOUT", default="20"))
        except ValueError:
            self.timeout = 20.0

    def config_problems(self):
        problems = []
        if not self.host:
            problems.append("SMTP_HOST is missing")
        if not 0 < self.port < 65536:
            problems.append("SMTP_PORT is not a valid port number")
        if self.security not in ("starttls", "ssl", "none"):
            problems.append("SMTP_SECURITY must be starttls, ssl or none")
        if not self.user:
            problems.append("SMTP_USER is missing")
        if not self.password:
            problems.append("SMTP_PASSWORD is missing")
        if not _EMAIL_RE.match(self.from_addr or ""):
            problems.append("SMTP_FROM (or SMTP_USER) must be a valid e-mail address")
        return problems

    def is_configured(self):
        return not self.config_problems()

    def settings_summary(self):
        """Non-secret settings for display in the UI."""
        return {
            "Host": self.host,
            "Port": self.port,
            "Security": self.security,
            "Login user": self.user or "(not set)",
            "Sender": self.from_addr or "(not set)",
            "Password": "set" if self.password else "NOT SET",
        }

    # -------------------------------------------------------------- connection
    def _open(self):
        context = ssl.create_default_context()
        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
            server.ehlo()
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            server.ehlo()
            if self.security == "starttls":
                if not server.has_extn("starttls"):
                    server.close()
                    raise smtplib.SMTPNotSupportedError(
                        "The server does not offer STARTTLS on this port"
                    )
                server.starttls(context=context)
                server.ehlo()
        try:
            server.login(self.user, self.password)
        except Exception:
            self._close(server)
            raise
        return server

    @staticmethod
    def _close(server):
        if server is None:
            return
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass

    def _explain(self, exc):
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            hint = ""
            if "gmail" in self.host.lower():
                hint = (" Gmail requires an App Password (Google Account > Security > "
                        "2-Step Verification > App passwords), not your normal password.")
            return f"Login rejected by {self.host} (code {exc.smtp_code}).{hint}"
        if isinstance(exc, smtplib.SMTPNotSupportedError):
            return f"{exc}. Try SMTP_SECURITY=ssl with port 465."
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            return "The recipient address was refused by the mail server."
        if isinstance(exc, smtplib.SMTPSenderRefused):
            return (f"The sender address {self.from_addr} was refused (code {exc.smtp_code}). "
                    "SMTP_FROM must belong to the account you log in with.")
        if isinstance(exc, ssl.SSLError):
            return (f"TLS handshake failed ({exc.__class__.__name__}). Port 465 needs "
                    "SMTP_SECURITY=ssl; port 587 needs SMTP_SECURITY=starttls.")
        if isinstance(exc, socket.gaierror):
            return f"Cannot resolve host '{self.host}'. Check SMTP_HOST and your internet connection."
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return (f"Connection to {self.host}:{self.port} timed out. Check the port and "
                    "whether a firewall or network blocks outgoing SMTP.")
        if isinstance(exc, ConnectionRefusedError):
            return f"Connection refused by {self.host}:{self.port}. Check SMTP_PORT."
        if isinstance(exc, smtplib.SMTPServerDisconnected):
            return ("The server closed the connection unexpectedly. The port and "
                    "SMTP_SECURITY setting probably do not match.")
        if isinstance(exc, smtplib.SMTPResponseException):
            msg = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else exc.smtp_error
            return f"Mail server error {exc.smtp_code}: {msg}"
        return f"{exc.__class__.__name__}: {exc}"

    @staticmethod
    def _is_transient(exc):
        if isinstance(exc, (smtplib.SMTPServerDisconnected, socket.timeout, TimeoutError,
                            ConnectionResetError, ConnectionAbortedError)):
            return True
        if isinstance(exc, smtplib.SMTPResponseException) and 400 <= exc.smtp_code < 500:
            return not isinstance(exc, smtplib.SMTPAuthenticationError)
        return False

    def test_connection(self):
        """Connect, negotiate TLS and log in without sending anything."""
        self._load()
        problems = self.config_problems()
        if problems:
            return False, "Email is not configured: " + "; ".join(problems)
        server = None
        try:
            server = self._open()
            return True, (f"Connected to {self.host}:{self.port} ({self.security}) "
                          f"and signed in as {self.user}.")
        except Exception as exc:
            return False, self._explain(exc)
        finally:
            self._close(server)

    # ----------------------------------------------------------------- sending
    def send(self, to_email, subject, text_body, html_body=None):
        self._load()
        problems = self.config_problems()
        if problems:
            return False, "Email is not configured: " + "; ".join(problems)
        to_email = (to_email or "").strip()
        if not _EMAIL_RE.match(to_email):
            return False, "The recipient e-mail address is not valid."

        msg = EmailMessage()
        msg["Subject"] = " ".join(str(subject).split())
        msg["From"] = formataddr((self.from_name, self.from_addr))
        msg["To"] = to_email
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.from_addr.split("@")[-1])
        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        last_error = "Unknown error"
        for attempt in (1, 2):
            server = None
            try:
                server = self._open()
                server.send_message(msg)
                return True, f"Email sent to {to_email}."
            except Exception as exc:
                last_error = self._explain(exc)
                log.warning("SMTP attempt %s failed: %s", attempt, last_error)
                if attempt == 1 and self._is_transient(exc):
                    time.sleep(1.5)
                    continue
                return False, last_error
            finally:
                self._close(server)
        return False, last_error

    # --------------------------------------------------------------- templates
    @staticmethod
    def _layout(title, inner_html):
        return (
            '<div style="background:#f4f6fb;padding:24px;font-family:Segoe UI,Arial,sans-serif;color:#1f2937;">'
            '<table role="presentation" width="100%" style="max-width:600px;margin:0 auto;background:#ffffff;'
            'border:1px solid #e5e7eb;border-radius:8px;border-collapse:separate;">'
            '<tr><td style="background:#2b3a9e;color:#ffffff;padding:18px 24px;border-radius:8px 8px 0 0;'
            f'font-size:18px;font-weight:600;">{escape(title)}</td></tr>'
            f'<tr><td style="padding:24px;font-size:15px;line-height:1.6;">{inner_html}</td></tr>'
            '<tr><td style="padding:14px 24px;color:#6b7280;font-size:12px;border-top:1px solid #e5e7eb;">'
            'SME Compliance and Document Intelligence Platform</td></tr></table></div>'
        )

    def send_welcome_email(self, to_email, full_name, username=None):
        name = escape(full_name or "there")
        user_line = f"Your username is {username}." if username else ""
        text = (f"Hello {full_name or 'there'},\n\nYour account has been created. {user_line}\n"
                "You can now upload documents and receive deadline reminders.\n\n"
                "For your security we never send passwords by e-mail.")
        inner = (f"<p>Hello {name},</p><p>Your account has been created."
                 + (f" Your username is <strong>{escape(username)}</strong>." if username else "")
                 + "</p><p>You can now upload documents and receive deadline reminders.</p>"
                 "<p style=\"color:#6b7280;\">For your security we never send passwords by e-mail.</p>")
        return self.send(to_email, "Welcome to SME Document Intelligence", text,
                         self._layout("Welcome", inner))

    def send_password_email(self, email, password, full_name, username=None):
        """Kept for backward compatibility. Passwords are never e-mailed."""
        return self.send_welcome_email(email, full_name, username)

    def send_test_email(self, to_email):
        text = "This is a test message. Your SMTP settings are working."
        inner = "<p>This is a test message.</p><p><strong>Your SMTP settings are working.</strong></p>"
        return self.send(to_email, "SMTP test message", text, self._layout("SMTP test", inner))

    def send_expiry_alert(self, to_email, full_name, alerts):
        """alerts: dicts with document_name, date_type, date_value, days_left, level, description."""
        if not alerts:
            return False, "There are no alerts to send."
        colors = {"expired": "#b91c1c", "critical": "#b91c1c", "warning": "#b45309", "upcoming": "#1d4ed8"}
        lines, rows = [], []
        for a in alerts:
            level = a.get("level", "upcoming")
            days = a.get("days_left") or 0
            when = f"{abs(days)} day(s) overdue" if days < 0 else ("today" if days == 0 else f"in {days} day(s)")
            label = str(a.get("date_type") or "").replace("_", " ").title()
            lines.append(f"- {a.get('document_name')}: {label} on {format_date(a.get('date_value'))} ({when})")
            rows.append(
                "<tr>"
                f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;">{escape(str(a.get("document_name", "")))}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;">{escape(label)}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;">{escape(format_date(a.get("date_value")))}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;color:{colors.get(level, "#1d4ed8")};'
                f'font-weight:600;">{escape(LEVEL_LABELS.get(level, level))} - {escape(when)}</td></tr>'
            )
        count = len(alerts)
        subject = f"{count} document deadline{'s' if count != 1 else ''} need your attention"
        text = f"Hello {full_name or 'there'},\n\nThe following deadlines are approaching:\n\n" + "\n".join(lines)
        inner = (f"<p>Hello {escape(full_name or 'there')},</p><p>The following deadlines need attention:</p>"
                 '<table style="width:100%;border-collapse:collapse;font-size:14px;">'
                 '<tr style="background:#f3f4f6;text-align:left;"><th style="padding:8px;">Document</th>'
                 '<th style="padding:8px;">Type</th><th style="padding:8px;">Date</th>'
                 f'<th style="padding:8px;">Status</th></tr>{"".join(rows)}</table>')
        return self.send(to_email, subject, text, self._layout("Deadline reminder", inner))
