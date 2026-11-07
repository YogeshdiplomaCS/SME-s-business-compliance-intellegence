import html
import inspect
import json
import os
import re
import time
from datetime import date

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import notifications  # e-mail helpers (verification code, welcome, document and alert e-mails)
from alerts import LEVEL_ORDER, build_alerts, format_date
from database import Database
from document_processor import DocumentProcessor
from email_handler import EmailHandler
from translations import get_all_languages, tx

load_dotenv()

st.set_page_config(page_title="SME Document Intelligence", page_icon=None, layout="wide",
                   initial_sidebar_state="expanded")

try:
    STRETCH = {"width": "stretch"} if "width" in inspect.signature(st.button).parameters else {"use_container_width": True}
except (TypeError, ValueError):
    STRETCH = {"use_container_width": True}

try:
    MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "10"))
    MAX_UPLOAD_MB = int(MAX_UPLOAD_MB) if MAX_UPLOAD_MB.is_integer() else MAX_UPLOAD_MB
except ValueError:
    MAX_UPLOAD_MB = 10
ALLOWED_EXT = ["pdf", "jpg", "jpeg", "png", "tif", "tiff", "bmp", "docx"]
CATEGORY_CHOICES = ["invoice", "contract", "license", "certificate", "purchase_order", "agreement", "policy", "other"]
STATUS_KIND = {"completed": "success", "processing": "info", "pending": "warning", "failed": "danger"}
LEVEL_KIND = {"expired": "danger", "critical": "danger", "warning": "warning", "upcoming": "info"}
LEVEL_ACCENT = {"expired": "#dc2626", "critical": "#dc2626", "warning": "#d97706", "upcoming": "#2563eb"}

PAGES = [("dashboard", "dashboard"), ("upload", "upload_document"), ("documents", "my_documents"),
         ("alerts", "expiry_alerts"), ("search", "search"), ("settings", "settings")]


# ----------------------------------------------------------------------------- helpers
def tr(key, default=None, **kwargs):
    """Translate into the language chosen by the user (English / Hindi / Kannada)."""
    value = tx(key, st.session_state.get("language", "en"), **kwargs)
    return default if (value == key and default is not None) else value


def esc(value):
    return html.escape("" if value is None else str(value))


def label(value):
    return str(value or "").replace("_", " ").title()


def label_tr(value):
    """Translated label for categories / statuses / levels, falling back to a tidy English label."""
    key = str(value or "")
    return tr(key, default=label(value)) if key else tr("uncategorized")


def type_label(value):
    key = "dt_" + str(value or "").strip().lower().replace(" ", "_")
    return tr(key, default=label(value))


def level_label(level):
    return tr("lvl_" + str(level), default=label(level))


def load_css():
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stApp { font-family: 'Inter', 'Segoe UI', sans-serif; }
.stApp { background: #f4f6fb; }
#MainMenu, footer, [data-testid="stDeployButton"] { visibility: hidden; }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1200px; }
[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #e5e7eb; }
[data-testid="stSidebar"] .block-container { padding-top: 1rem; }
h1, h2, h3 { color: #111827; letter-spacing: -0.01em; }
h3 { font-size: 1.1rem !important; font-weight: 600 !important; margin-top: 0.4rem; }
.page-header { background: linear-gradient(120deg, #1e2a78 0%, #3b4cca 100%); color: #fff; padding: 1.5rem 1.8rem;
  border-radius: 12px; margin-bottom: 1.4rem; }
.page-header h1 { color: #fff; font-size: 1.7rem; font-weight: 700; margin: 0; padding: 0; }
.page-header p { color: #dbe1ff; margin: 0.35rem 0 0 0; font-size: 0.98rem; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 1.1rem 1.25rem; margin-bottom: 0.9rem; }
.card h4 { margin: 0 0 0.35rem 0; font-size: 1rem; color: #111827; font-weight: 600; }
.card .meta { color: #6b7280; font-size: 0.85rem; margin-top: 0.4rem; }
.stat-card { background: #fff; border: 1px solid #e5e7eb; border-top: 4px solid var(--accent, #3b4cca);
  border-radius: 10px; padding: 0.9rem 1rem; }
.stat-card .value { font-size: 1.9rem; font-weight: 700; color: #111827; line-height: 1.1; }
.stat-card .caption { color: #6b7280; font-size: 0.82rem; margin-top: 0.25rem; }
.badge { display: inline-block; padding: 0.18rem 0.65rem; border-radius: 999px; font-size: 0.72rem; font-weight: 600;
  letter-spacing: 0.02em; margin-right: 0.35rem; }
.badge-success { background: #dcfce7; color: #166534; }
.badge-warning { background: #fef3c7; color: #92400e; }
.badge-danger { background: #fee2e2; color: #991b1b; }
.badge-info { background: #dbeafe; color: #1e40af; }
.badge-neutral { background: #eef0f6; color: #374151; }
.deadline { background: #fff; border: 1px solid #e5e7eb; border-left: 5px solid var(--accent, #3b4cca); border-radius: 10px;
  padding: 0.8rem 1rem; margin-bottom: 0.6rem; display: flex; justify-content: space-between; gap: 1rem; align-items: center; }
.deadline .title { font-weight: 600; color: #111827; }
.deadline .sub { color: #6b7280; font-size: 0.85rem; }
.deadline .days { text-align: right; white-space: nowrap; }
.deadline .days strong { font-size: 1.3rem; color: var(--accent, #3b4cca); }
.table-wrap { overflow-x: auto; border: 1px solid #e5e7eb; border-radius: 10px; background: #fff; margin-bottom: 0.8rem; }
.data-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.data-table th { background: #f3f4f8; text-align: left; padding: 0.6rem 0.8rem; color: #374151; font-weight: 600;
  border-bottom: 1px solid #e5e7eb; }
.data-table td { padding: 0.55rem 0.8rem; border-bottom: 1px solid #eef0f4; color: #1f2937; vertical-align: top; word-break: break-word; }
.data-table tr:last-child td { border-bottom: none; }
.empty { text-align: center; color: #6b7280; padding: 1.6rem; background: #fff; border: 1px dashed #cbd2e1; border-radius: 12px; }
.section-note { color: #6b7280; font-size: 0.88rem; margin: -0.2rem 0 0.8rem 0; }
div.stButton > button, div.stDownloadButton > button, div[data-testid="stFormSubmitButton"] > button {
  border-radius: 8px; font-weight: 600; }
div[data-testid="stForm"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 1.2rem; }
</style>
""", unsafe_allow_html=True)


def page_header(title, subtitle=""):
    sub = f"<p>{esc(subtitle)}</p>" if subtitle else ""
    st.markdown(f'<div class="page-header"><h1>{esc(title)}</h1>{sub}</div>', unsafe_allow_html=True)


def badge(text, kind="neutral"):
    return f'<span class="badge badge-{kind}">{esc(text)}</span>'


def stat_card(value, caption, accent="#3b4cca"):
    return (f'<div class="stat-card" style="--accent:{accent}"><div class="value">{esc(value)}</div>'
            f'<div class="caption">{esc(caption)}</div></div>')


def html_table(headers, rows):
    """Cells in rows must already be HTML-escaped."""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table class="data-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def empty_state(text):
    st.markdown(f'<div class="empty">{esc(text)}</div>', unsafe_allow_html=True)


def flash(kind, message):
    st.session_state.flash.append((kind, message))


def show_flash():
    for kind, message in st.session_state.flash:
        getattr(st, kind)(message)
    st.session_state.flash = []


def load_json(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}


# ----------------------------------------------------------------------------- state
def init_session_state():
    defaults = {"logged_in": False, "user_id": None, "username": "", "language": "en", "auth_mode": "login",
                "nav_page": "dashboard", "selected_doc": None, "confirm_delete": None, "last_result": None,
                "flash": [], "pending_reg": None}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


@st.cache_resource
def get_db():
    return Database()


@st.cache_resource
def get_email_handler():
    return EmailHandler()


@st.cache_resource
def get_processor():
    return DocumentProcessor()


def nav_changed():
    st.session_state.selected_doc = None
    st.session_state.confirm_delete = None


def go(page):
    st.session_state.nav_page = page
    st.session_state.selected_doc = None
    st.session_state.confirm_delete = None


def set_auth_mode(mode):
    st.session_state.auth_mode = mode


def open_doc(doc_id):
    st.session_state.nav_page = "documents"
    st.session_state.selected_doc = doc_id
    st.session_state.confirm_delete = None


def close_doc():
    st.session_state.selected_doc = None


def ask_delete(doc_id):
    st.session_state.confirm_delete = doc_id


def cancel_delete():
    st.session_state.confirm_delete = None


def change_language(widget_key):
    st.session_state.language = st.session_state[widget_key]
    if st.session_state.logged_in:
        get_db().update_language_preference(st.session_state.user_id, st.session_state.language)


def language_selector(widget_key, caption=None):
    languages = get_all_languages()
    codes = list(languages.keys())
    index = codes.index(st.session_state.language) if st.session_state.language in codes else 0
    st.selectbox(caption or tr("language"), codes, index=index, key=widget_key,
                 format_func=lambda c: languages[c], on_change=change_language, args=(widget_key,))


# ----------------------------------------------------------------------------- validation
def validate_password(password):
    if (len(password) < 8 or not re.search(r"[A-Z]", password) or not re.search(r"\d", password)
            or not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=]', password)):
        return False, tr("password_validation")
    return True, ""


def validate_mobile(mobile):
    return (True, "") if re.match(r"^\d{10}$", mobile or "") else (False, tr("mobile_validation"))


def validate_email(email):
    ok = re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email or "")
    return (True, "") if ok else (False, tr("email_validation"))


# ----------------------------------------------------------------------------- auth pages
def auth_header():
    page_header(tr("app_title"), tr("app_subtitle"))


def show_login_page():
    auth_header()
    _, col, _ = st.columns([1, 1.6, 1])
    with col:
        language_selector("lang_auth")
        show_flash()
        st.markdown(f"### {esc(tr('login_title'))}", unsafe_allow_html=True)
        with st.form("login_form"):
            username = st.text_input(tr("username"))
            password = st.text_input(tr("password"), type="password")
            submitted = st.form_submit_button(tr("login_button"), type="primary", **STRETCH)
        if submitted:
            if not username.strip() or not password:
                st.error(tr("field_required"))
            else:
                db = get_db()
                user = db.login_user(username, password)
                if user:
                    st.session_state.update(logged_in=True, user_id=user["id"], username=user["username"],
                                            nav_page="dashboard", selected_doc=None)
                    db.update_language_preference(user["id"], st.session_state.language)
                    st.rerun()
                elif db.last_error:
                    st.error(f"{tr('database_error')}: {db.last_error}")
                else:
                    st.error(tr("invalid_credentials"))
        st.caption(tr("no_account"))
        st.button(tr("register_title"), on_click=set_auth_mode, args=("register",), key="to_register", **STRETCH)


def issue_code(pend):
    """Create a fresh 6-digit code, e-mail it, and store only its salted hash in `pend`."""
    code = notifications.generate_code()
    salt = os.urandom(8).hex()
    ok, message = notifications.send_verification_email(pend["email"], pend["full_name"], code,
                                                        st.session_state.get("language", "en"))
    if ok:
        pend.update(salt=salt, code_hash=notifications.hash_code(code, salt),
                    expires=time.time() + notifications.CODE_VALID_MINUTES * 60, attempts=0, sent_at=time.time())
    return ok, message


def cancel_verification():
    st.session_state.pending_reg = None
    st.session_state.auth_mode = "register"


def show_register_page():
    auth_header()
    _, col, _ = st.columns([1, 1.6, 1])
    with col:
        language_selector("lang_auth")
        st.markdown(f"### {esc(tr('register_title'))}", unsafe_allow_html=True)
        with st.form("register_form"):
            full_name = st.text_input(tr("full_name"))
            username = st.text_input(tr("username"))
            email = st.text_input(tr("email"))
            mobile = st.text_input(tr("mobile"), max_chars=10)
            password = st.text_input(tr("password"), type="password")
            confirm = st.text_input(tr("confirm_password"), type="password")
            st.caption(tr("password_validation"))
            submitted = st.form_submit_button(tr("register_button"), type="primary", **STRETCH)
        if submitted:
            errors = []
            if not full_name.strip():
                errors.append(f"{tr('full_name')}: {tr('field_required')}")
            if len(username.strip()) < 3:
                errors.append(tr("min_username"))
            if not validate_email(email.strip())[0]:
                errors.append(tr("email_validation"))
            if not validate_mobile(mobile.strip())[0]:
                errors.append(tr("mobile_validation"))
            if not validate_password(password)[0]:
                errors.append(tr("password_validation"))
            elif password != confirm:
                errors.append(tr("password_match"))
            db = get_db()
            if not errors:
                if db.username_exists(username.strip()):
                    errors.append(tr("username_exists"))
                if db.email_exists(email.strip()):
                    errors.append(tr("email_exists"))
            for message in errors:
                st.error(message)
            if not errors:
                mail_problems = notifications.config_problems()
                if mail_problems:
                    st.error(f"{tr('email_not_configured')}: {'; '.join(mail_problems)}")
                else:
                    pend = {"full_name": full_name.strip(), "username": username.strip(), "email": email.strip(),
                            "mobile": mobile.strip(), "password": password}
                    with st.spinner(tr("sending")):
                        ok, message = issue_code(pend)
                    if ok:
                        st.session_state.pending_reg = pend
                        st.session_state.auth_mode = "verify"
                        st.rerun()
                    else:
                        st.error(f"{tr('verify_send_failed')}. {message}")
        st.caption(tr("have_account"))
        st.button(tr("login_title"), on_click=set_auth_mode, args=("login",), key="to_login", **STRETCH)


def complete_registration(pend):
    """Create the account after the e-mail address has been verified, then log the user in."""
    db = get_db()
    problems = []
    if db.username_exists(pend["username"]):
        problems.append(tr("username_exists"))
    if db.email_exists(pend["email"]):
        problems.append(tr("email_exists"))
    if problems:
        for message in problems:
            st.error(message)
        return
    user_id = db.register_user(pend["full_name"], pend["email"], pend["mobile"], pend["username"], pend["password"])
    if not user_id:
        st.error(f"{tr('registration_failed')}. {db.last_error or tr('try_again')}")
        return
    lang = st.session_state.language
    db.update_language_preference(user_id, lang)
    ok, message = notifications.send_welcome_email(pend["email"], pend["full_name"], pend["username"], lang)
    flash("success", tr("reg_done"))
    if not ok:
        flash("warning", f"{tr('welcome_email_failed')}. {message}")
    st.session_state.update(logged_in=True, user_id=user_id, username=pend["username"], pending_reg=None,
                            nav_page="dashboard", selected_doc=None)
    st.rerun()


def show_verify_page():
    pend = st.session_state.pending_reg
    if not pend:
        st.session_state.auth_mode = "register"
        st.rerun()
        return
    auth_header()
    _, col, _ = st.columns([1, 1.6, 1])
    with col:
        language_selector("lang_auth")
        st.markdown(f"### {esc(tr('verify_title'))}", unsafe_allow_html=True)
        st.info(tr("verify_sent", email=pend["email"]))
        with st.form("verify_form"):
            code = st.text_input(tr("verify_code"), max_chars=6)
            submitted = st.form_submit_button(tr("verify_button"), type="primary", **STRETCH)
        if submitted:
            code = (code or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                st.error(tr("code_format"))
            elif time.time() > pend["expires"]:
                st.error(tr("code_expired"))
            elif pend["attempts"] >= 5:
                st.error(tr("too_many_attempts"))
            else:
                pend["attempts"] += 1
                if notifications.code_matches(code, pend["salt"], pend["code_hash"]):
                    complete_registration(pend)
                else:
                    st.error(tr("code_invalid", left=max(0, 5 - pend["attempts"])))
        c1, c2 = st.columns(2)
        if c1.button(tr("resend_code"), key="resend_code", **STRETCH):
            wait = int(30 - (time.time() - pend.get("sent_at", 0)))
            if wait > 0:
                st.warning(tr("resend_wait", seconds=wait))
            else:
                with st.spinner(tr("sending")):
                    ok, message = issue_code(pend)
                if ok:
                    st.success(tr("code_resent", email=pend["email"]))
                else:
                    st.error(f"{tr('verify_send_failed')}. {message}")
        c2.button(tr("back_to_register"), on_click=cancel_verification, key="back_register", **STRETCH)


# ----------------------------------------------------------------------------- sidebar
def show_sidebar():
    with st.sidebar:
        user = get_db().get_user_by_id(st.session_state.user_id)
        if user:
            st.markdown(f'<div class="card" style="padding:0.9rem 1rem"><h4>{esc(user["full_name"])}</h4>'
                        f'<div class="meta" style="margin-top:0">{esc(user["email"])}</div></div>', unsafe_allow_html=True)
        st.radio(tr("navigation"), [p[0] for p in PAGES], key="nav_page", label_visibility="collapsed",
                 format_func=lambda p: tr(dict(PAGES)[p]), on_change=nav_changed)
        st.divider()
        language_selector("lang_sidebar")
        if st.button(tr("logout"), key="logout", **STRETCH):
            for key in ("logged_in", "user_id", "selected_doc", "confirm_delete", "last_result", "pending_reg"):
                st.session_state[key] = False if key == "logged_in" else None
            st.session_state.update(username="", auth_mode="login")
            st.rerun()


# ----------------------------------------------------------------------------- shared renderers
def render_deadline(alert):
    level = alert.get("level", "upcoming")
    accent = LEVEL_ACCENT.get(level, "#3b4cca")
    days = alert.get("days_left", 0)
    big = f"{abs(days)}"
    unit = tr("days_overdue") if days < 0 else tr("days_left")
    sub = f'{esc(type_label(alert.get("date_type")))} - {esc(alert.get("description") or "")}'
    st.markdown(
        f'<div class="deadline" style="--accent:{accent}"><div><div class="title">{esc(alert.get("document_name") or tr("document"))}</div>'
        f'<div class="sub">{sub}</div><div style="margin-top:.35rem">{badge(level_label(level), LEVEL_KIND.get(level, "info"))}'
        f'<span class="sub">{esc(format_date(alert.get("date_value")))}</span></div></div>'
        f'<div class="days"><strong>{big}</strong><div class="sub">{esc(unit)}</div></div></div>', unsafe_allow_html=True)


def render_analysis(result):
    """Show the outcome of an analysis (dict from DocumentProcessor)."""
    try:
        conf = int(round(float(result.get("confidence", 0) or 0) * 100))
    except (TypeError, ValueError):
        conf = 0
    method = tr("ai_analysis") if result.get("method") == "ai" else tr("rule_analysis")
    st.markdown(
        f'<div class="card"><h4>{esc(label_tr(result.get("category")))}</h4>'
        f'{badge(tr("confidence_pct", pct=conf), "success" if conf >= 70 else "warning")}{badge(method, "neutral")}'
        f'<p style="margin:.7rem 0 0 0">{esc(result.get("summary") or "")}</p>'
        f'<div class="meta">{esc(result.get("reason") or "")}</div></div>', unsafe_allow_html=True)
    if result.get("warning"):
        st.warning(result["warning"])
    left, right = st.columns(2)
    with left:
        st.markdown(f"### {esc(tr('important_dates'))}", unsafe_allow_html=True)
        dates = result.get("dates") or []
        if dates:
            rows = [[esc(type_label(d.get("date_type"))), esc(format_date(d.get("date_value"))), esc(d.get("description", ""))]
                    for d in dates]
            st.markdown(html_table([tr("col_type"), tr("col_date"), tr("col_details")], rows), unsafe_allow_html=True)
        else:
            empty_state(tr("no_dates_found"))
    with right:
        st.markdown(f"### {esc(tr('extracted_information'))}", unsafe_allow_html=True)
        fields = {k: v for k, v in (result.get("fields") or {}).items() if k != "summary"}
        if fields:
            rows = [[esc(label(k)), esc(v if not isinstance(v, (list, dict)) else json.dumps(v, ensure_ascii=False))]
                    for k, v in fields.items()]
            st.markdown(html_table([tr("col_field"), tr("col_value")], rows), unsafe_allow_html=True)
        else:
            empty_state(tr("no_extracted_info"))


# ----------------------------------------------------------------------------- dashboard
def show_dashboard():
    db = get_db()
    uid = st.session_state.user_id
    page_header(f"{tr('welcome')}, {st.session_state.username}", tr("app_subtitle"))
    show_flash()
    stats = db.get_user_stats(uid)
    if stats is None:
        st.error(f"{tr('stats_failed')} {db.last_error or ''}")
        stats = {}
    cards = [("total_documents", "total_documents", "#3b4cca"), ("completed_documents", "processed_documents", "#16a34a"),
             ("pending_documents", "pending_documents", "#d97706"), ("invoices", "invoices", "#2563eb"),
             ("contracts", "contracts", "#dc2626"), ("licenses", "licenses", "#7c3aed")]
    for col, (key, caption, accent) in zip(st.columns(len(cards)), cards):
        col.markdown(stat_card(stats.get(key, 0), tr(caption), accent), unsafe_allow_html=True)
    st.write("")
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"### {esc(tr('upcoming_expiries'))}", unsafe_allow_html=True)
        alerts = build_alerts(db.get_user_important_dates(uid) or [])
        if alerts:
            for alert in alerts[:5]:
                render_deadline(alert)
            if len(alerts) > 5:
                st.button(tr("view_all_alerts", n=len(alerts)), on_click=go, args=("alerts",), key="dash_all_alerts")
        else:
            empty_state(tr("no_expiries"))
    with right:
        st.markdown(f"### {esc(tr('documents_by_category'))}", unsafe_allow_html=True)
        counts = db.get_category_counts(uid)
        if counts:
            st.bar_chart(pd.DataFrame({tr("my_documents"): list(counts.values())}, index=[label_tr(k) for k in counts]))
        else:
            empty_state(tr("no_documents"))
        st.markdown(f"### {esc(tr('quick_actions'))}", unsafe_allow_html=True)
        st.button(tr("upload_document"), on_click=go, args=("upload",), key="qa_upload", type="primary", **STRETCH)
        st.button(tr("my_documents"), on_click=go, args=("documents",), key="qa_docs", **STRETCH)
        st.button(tr("search"), on_click=go, args=("search",), key="qa_search", **STRETCH)


# ----------------------------------------------------------------------------- upload
def show_upload_page():
    page_header(tr("upload_document"), tr("upload_instructions"))
    left, right = st.columns([1, 1.4])
    with left:
        st.markdown(f"### {esc(tr('guidelines'))}", unsafe_allow_html=True)
        st.markdown(f"- {tr('supported_formats')}\n- {tr('max_file_size')}\n- {tr('ocr_note')}\n- {tr('clear_pages_note')}")
        if not get_processor().ai_available():
            st.warning(tr("no_api_key_upload"))
    with right:
        uploaded = st.file_uploader(tr("upload_button"), type=ALLOWED_EXT, help=tr("supported_formats"))
        if uploaded is not None:
            size_mb = uploaded.size / (1024 * 1024)
            st.markdown(f'<div class="card"><h4>{esc(uploaded.name)}</h4><div class="meta">{size_mb:.2f} MB</div></div>',
                        unsafe_allow_html=True)
            if size_mb > MAX_UPLOAD_MB:
                st.error(tr("file_too_large", size=size_mb, limit=MAX_UPLOAD_MB))
            elif st.button(tr("analyse_document"), type="primary", key="process_doc", **STRETCH):
                folder = os.getenv("UPLOAD_FOLDER", "uploads")
                os.makedirs(folder, exist_ok=True)
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(uploaded.name))
                path = os.path.join(folder, f"{date.today():%Y%m%d}_{os.urandom(4).hex()}_{safe}")
                with open(path, "wb") as fh:
                    fh.write(uploaded.getvalue())
                with st.spinner(tr("reading_spinner")):
                    doc_id, message, result = get_processor().process_document(path, uploaded.name, st.session_state.user_id, get_db())
                last = {"doc_id": doc_id, "message": message, "result": result, "name": uploaded.name,
                        "expired": 0, "email": None}
                if result:
                    # e-mail the user about every analysed document (and flag expired dates)
                    last["expired"] = notifications.classify_dates(result.get("dates"))[1]
                    user = get_db().get_user_by_id(st.session_state.user_id)
                    if user and user.get("email"):
                        with st.spinner(tr("sending")):
                            ok, mail_message, _ = notifications.send_document_email(
                                user["email"], user.get("full_name") or st.session_state.username, uploaded.name,
                                result, st.session_state.language)
                        last["email"] = {"ok": ok, "message": mail_message, "to": user["email"]}
                st.session_state.last_result = last
    last = st.session_state.last_result
    if last:
        st.divider()
        if last["result"]:
            st.success(f"{last['name']}: {last['message']}")
            if last.get("expired"):
                st.error(tr("doc_has_expired"))
            mail = last.get("email")
            if mail:
                if mail["ok"]:
                    st.info(tr("upload_email_sent", email=mail["to"]))
                else:
                    st.warning(f"{tr('upload_email_failed')}. {mail['message']}")
            render_analysis(last["result"])
        else:
            st.error(last["message"])
        if last["doc_id"]:
            st.button(tr("open_record"), on_click=open_doc, args=(last["doc_id"],), key="open_last")


# ----------------------------------------------------------------------------- documents
def show_documents_page():
    if st.session_state.selected_doc:
        return show_document_detail(st.session_state.selected_doc)
    db = get_db()
    page_header(tr("my_documents"))
    show_flash()
    c1, c2, c3 = st.columns(3)
    status_filter = c1.selectbox(tr("status"), ["all", "pending", "processing", "completed", "failed"],
                                 format_func=lambda v: tr("all") if v == "all" else label_tr(v))
    category_filter = c2.selectbox(tr("category"), ["all"] + CATEGORY_CHOICES,
                                   format_func=lambda v: tr("all") if v == "all" else label_tr(v))
    term = c3.text_input(tr("search"), key="doc_filter")
    documents = db.get_user_documents(st.session_state.user_id) or []
    if status_filter != "all":
        documents = [d for d in documents if d["status"] == status_filter]
    if category_filter != "all":
        documents = [d for d in documents if d.get("category") == category_filter]
    if term:
        documents = [d for d in documents if term.lower() in d["document_name"].lower()]
    if not documents:
        return empty_state(tr("no_documents"))
    st.caption(tr("documents_count", n=len(documents)))
    for doc in documents:
        st.markdown(
            f'<div class="card"><h4>{esc(doc["document_name"])}</h4>'
            f'{badge(label_tr(doc.get("category")), "neutral")}'
            f'{badge(label_tr(doc["status"]), STATUS_KIND.get(doc["status"], "info"))}'
            f'<div class="meta">{tr("upload_date")}: {esc(doc["uploaded_at"])}</div></div>', unsafe_allow_html=True)
        b1, b2, _ = st.columns([1, 1, 4])
        b1.button(tr("view"), key=f"view_{doc['id']}", on_click=open_doc, args=(doc["id"],), **STRETCH)
        if st.session_state.confirm_delete == doc["id"]:
            if b2.button(tr("confirm_delete"), key=f"confirm_{doc['id']}", type="primary", **STRETCH):
                deleted = db.delete_document(doc["id"], st.session_state.user_id)
                if deleted and deleted.get("file_path") and os.path.exists(deleted["file_path"]):
                    try:
                        os.remove(deleted["file_path"])
                    except OSError:
                        pass
                st.session_state.confirm_delete = None
                flash("success" if deleted else "error", tr("delete_success") if deleted else tr("delete_failed"))
                st.rerun()
            st.button(tr("cancel"), key=f"cancel_{doc['id']}", on_click=cancel_delete)
        else:
            b2.button(tr("delete"), key=f"delete_{doc['id']}", on_click=ask_delete, args=(doc["id"],), **STRETCH)


def show_document_detail(doc_id):
    db = get_db()
    doc = db.get_document_by_id(doc_id, st.session_state.user_id)
    if not doc:
        st.error(tr("doc_not_found"))
        st.button(tr("close"), on_click=close_doc)
        return
    page_header(doc["document_name"], tr("document_details"))
    st.button(tr("back_to_docs"), on_click=close_doc, key="back_docs")
    meta = load_json(doc.get("metadata"))
    left, right = st.columns([1, 1.4])
    with left:
        rows = [[esc(tr("col_name")), esc(doc["document_name"])],
                [esc(tr("category")), esc(label_tr(doc.get("category")))],
                [esc(tr("status")), badge(label_tr(doc["status"]), STATUS_KIND.get(doc["status"], "info"))],
                [esc(tr("col_uploaded")), esc(doc["uploaded_at"])]]
        st.markdown(html_table([tr("col_property"), tr("col_value")], rows), unsafe_allow_html=True)
        path = doc.get("file_path")
        if path and os.path.exists(path):
            with open(path, "rb") as fh:
                st.download_button(tr("download_original"), fh.read(), file_name=doc["document_name"],
                                   key="dl_original", **STRETCH)
        if st.button(tr("rerun_analysis"), key="reanalyze", **STRETCH):
            with st.spinner(tr("analysing")):
                _, message, result = get_processor().reanalyze(doc_id, st.session_state.user_id, db)
            flash("success" if result else "error", message)
            st.rerun()
    with right:
        cat = meta.get("categorization", {}) or {}
        if meta:
            method = tr("ai_analysis") if meta.get("analysis_method") == "ai" else tr("rule_analysis")
            try:
                conf = int(round(float(cat.get("confidence", 0) or 0) * 100))
            except (TypeError, ValueError):
                conf = 0
            st.markdown(f'<div class="card"><h4>{esc(tr("summary"))}</h4>{badge(method, "neutral")}{badge(tr("confidence_pct", pct=conf), "info")}'
                        f'<p style="margin:.7rem 0 0 0">{esc(meta.get("summary") or tr("no_summary"))}</p>'
                        f'<div class="meta">{esc(cat.get("reason", ""))}</div></div>', unsafe_allow_html=True)
            if meta.get("warning"):
                st.warning(meta["warning"])
        elif doc["status"] == "failed":
            st.error(tr("analysis_failed_msg"))
        else:
            empty_state(tr("not_analysed"))
    show_flash()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"### {esc(tr('important_dates'))}", unsafe_allow_html=True)
        dates = db.get_document_dates(doc_id) or []
        if dates:
            rows = [[esc(type_label(d["date_type"])), esc(format_date(d["date_value"])), esc(d.get("description"))] for d in dates]
            st.markdown(html_table([tr("col_type"), tr("col_date"), tr("col_details")], rows), unsafe_allow_html=True)
        else:
            empty_state(tr("no_important_dates"))
    with c2:
        st.markdown(f"### {esc(tr('extracted_information'))}", unsafe_allow_html=True)
        info = [i for i in (db.get_extracted_info(doc_id) or []) if i["field_name"] != "summary"]
        if info:
            rows = [[esc(label(i["field_name"])), esc(i["field_value"])] for i in info]
            st.markdown(html_table([tr("col_field"), tr("col_value")], rows), unsafe_allow_html=True)
        else:
            empty_state(tr("no_extracted_info"))
    if doc.get("extracted_text"):
        with st.expander(tr("extracted_text")):
            st.text_area(tr("extracted_text"), doc["extracted_text"], height=300, label_visibility="collapsed")


# ----------------------------------------------------------------------------- alerts
def show_alerts_page():
    db = get_db()
    page_header(tr("expiry_alerts"), tr("alerts_subtitle"))
    show_flash()
    alerts = build_alerts(db.get_user_important_dates(st.session_state.user_id) or [])
    if not alerts:
        return empty_state(tr("no_alerts_30"))
    accents = {"expired": "#dc2626", "critical": "#ea580c", "warning": "#d97706", "upcoming": "#2563eb"}
    for col, level in zip(st.columns(len(LEVEL_ORDER)), LEVEL_ORDER):
        col.markdown(stat_card(sum(1 for a in alerts if a["level"] == level), level_label(level), accents[level]),
                     unsafe_allow_html=True)
    st.write("")
    user = db.get_user_by_id(st.session_state.user_id)
    if user and st.button(tr("email_alerts_btn"), key="email_alerts", type="primary"):
        with st.spinner(tr("sending")):
            ok, message = notifications.send_alert_digest(user["email"], user["full_name"], alerts,
                                                          st.session_state.language)
        if ok:
            for a in alerts:
                if a.get("id") and a["level"] != "expired":
                    db.mark_alert_sent(a["id"])
            flash("success", tr("alerts_email_sent", email=user["email"]))
        else:
            flash("error", f"{tr('alerts_email_failed')}. {message}")
        st.rerun()
    for level in LEVEL_ORDER:
        group = [a for a in alerts if a["level"] == level]
        if group:
            st.markdown(f"### {esc(level_label(level))} ({len(group)})", unsafe_allow_html=True)
            for alert in group:
                render_deadline(alert)


# ----------------------------------------------------------------------------- search
def show_search_page():
    db = get_db()
    page_header(tr("search"), tr("semantic_search"))
    with st.form("search_form"):
        c1, c2 = st.columns([4, 1])
        query = c1.text_input(tr("search_placeholder"), label_visibility="collapsed", placeholder=tr("search_placeholder"))
        submitted = c2.form_submit_button(tr("search_button"), type="primary", **STRETCH)
    if submitted and query.strip():
        documents = db.get_all_documents_for_search(st.session_state.user_id) or []
        if not documents:
            st.warning(tr("no_analysed_docs"))
        else:
            with st.spinner(tr("searching")):
                results = get_processor().semantic_search(query.strip(), documents, st.session_state.language)
            db.save_search_history(st.session_state.user_id, query.strip(), "semantic", len(results))
            if results:
                st.markdown(f"### {esc(tr('search_results'))} ({len(results)})", unsafe_allow_html=True)
                for r in results:
                    pct = int(r["relevance_score"] * 100)
                    rel_text = f"{pct}% {tr('relevance')}"
                    doc_title = r.get("document_name") or f"{tr('document')} {r['document_id']}"
                    st.markdown(
                        f'<div class="card"><h4>{esc(doc_title)}</h4>'
                        f'{badge(label_tr(r.get("category")), "neutral")}{badge(rel_text, "success")}'
                        f'<p style="margin:.6rem 0 0 0">{esc(r.get("snippet", ""))}</p>'
                        f'<div class="meta">{esc(r.get("reason", ""))}</div></div>', unsafe_allow_html=True)
                    st.button(tr("view"), key=f"sr_{r['document_id']}", on_click=open_doc, args=(r["document_id"],))
            else:
                empty_state(tr("no_results"))
    st.markdown(f"### {esc(tr('search_history'))}", unsafe_allow_html=True)
    history = db.get_search_history(st.session_state.user_id, 5) or []
    if history:
        rows = [[esc(h["query"]), esc(h.get("results_count", "")), esc(h["searched_at"])] for h in history]
        st.markdown(html_table([tr("col_query"), tr("col_results"), tr("col_when")], rows), unsafe_allow_html=True)
    else:
        empty_state(tr("no_searches"))


# ----------------------------------------------------------------------------- settings
def show_settings_page():
    db = get_db()
    page_header(tr("settings"))
    show_flash()
    user = db.get_user_by_id(st.session_state.user_id)
    if not user:
        return st.error(tr("user_not_found"))
    left, right = st.columns(2)
    with left:
        st.markdown(f"### {esc(tr('profile'))}", unsafe_allow_html=True)
        with st.form("profile_form"):
            full_name = st.text_input(tr("full_name"), value=user["full_name"])
            email = st.text_input(tr("email"), value=user["email"])
            mobile = st.text_input(tr("mobile"), value=user["mobile"], max_chars=10)
            saved = st.form_submit_button(tr("save_changes"), type="primary", **STRETCH)
        if saved:
            problems = []
            if not full_name.strip():
                problems.append(tr("field_required"))
            email, mobile, full_name = email.strip(), mobile.strip(), full_name.strip()
            if not validate_email(email)[0]:
                problems.append(tr("email_validation"))
            if not validate_mobile(mobile)[0]:
                problems.append(tr("mobile_validation"))
            if not problems and db.email_exists(email, exclude_user_id=user["id"]):
                problems.append(tr("email_exists"))
            if problems:
                for p in problems:
                    st.error(p)
            elif db.update_profile(user["id"], full_name, email, mobile) is None:
                st.error(f"{tr('save_failed')} {db.last_error or ''}")
            else:
                flash("success", tr("changes_saved"))
                st.rerun()
    with right:
        st.markdown(f"### {esc(tr('smtp_title'))}", unsafe_allow_html=True)
        handler = get_email_handler()
        handler._load()
        problems = handler.config_problems()
        rows = [[esc(k), esc(v)] for k, v in handler.settings_summary().items()]
        st.markdown(html_table([tr("col_setting"), tr("col_value")], rows), unsafe_allow_html=True)
        if problems:
            st.error(f"{tr('config_problems')}: " + "; ".join(problems) + f". {tr('edit_env')}")
        b1, b2 = st.columns(2)
        if b1.button(tr("test_connection"), key="smtp_test", disabled=bool(problems), **STRETCH):
            with st.spinner(tr("connecting")):
                ok, message = handler.test_connection()
            (st.success if ok else st.error)(message)
        if b2.button(tr("send_test_email"), key="smtp_send", disabled=bool(problems), **STRETCH):
            with st.spinner(tr("sending")):
                ok, message = handler.send_test_email(user["email"])
            (st.success if ok else st.error)(message)
        st.markdown(f"### {esc(tr('ai_service'))}", unsafe_allow_html=True)
        processor = get_processor()
        if not processor.ai_available():
            st.warning(tr("no_key_warning"))
        elif st.button(tr("check_ai"), key="ai_check", **STRETCH):
            with st.spinner(tr("contacting_ai")):
                ok, message = processor.check_api()
            (st.success if ok else st.error)(message)
    st.divider()
    st.markdown(f"### {esc(tr('about'))}", unsafe_allow_html=True)
    st.markdown(f"**{tr('version')}** 1.2.0  \n{tr('about_text')}")


# ----------------------------------------------------------------------------- main
def main():
    init_session_state()
    load_css()
    if not st.session_state.logged_in:
        mode = st.session_state.auth_mode
        if mode == "verify" and st.session_state.pending_reg:
            show_verify_page()
        elif mode == "register":
            show_register_page()
        else:
            show_login_page()
        return
    show_sidebar()
    {"dashboard": show_dashboard, "upload": show_upload_page, "documents": show_documents_page,
     "alerts": show_alerts_page, "search": show_search_page, "settings": show_settings_page,
     }.get(st.session_state.nav_page, show_dashboard)()


if __name__ == "__main__":
    main()
