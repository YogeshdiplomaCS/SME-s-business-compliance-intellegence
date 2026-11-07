"""notifications.py -- localised e-mails: verification code, welcome, document-received (with EXPIRED notice),
alert digest.

Sending is delegated to email_handler.EmailHandler, so there is a single SMTP implementation and a single
set of .env settings (SMTP_HOST, SMTP_PORT, SMTP_SECURITY, SMTP_USER, SMTP_PASSWORD, SMTP_FROM ...).
"""
import hashlib
import hmac
import html
import secrets
from datetime import date, datetime

from alerts import format_date, is_deadline, to_date
from email_handler import EmailHandler
from translations import tx

CODE_VALID_MINUTES = 10


# ----------------------------------------------------------------------------- sending
def config_problems():
    return EmailHandler().config_problems()


def _send(to_email, subject, text, html_body):
    return EmailHandler().send(to_email, subject, text, html_body)


# ----------------------------------------------------------------------------- verification codes
def generate_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code, salt):
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def code_matches(code, salt, expected_hash):
    return hmac.compare_digest(hash_code((code or "").strip(), salt), expected_hash)


# ----------------------------------------------------------------------------- html helpers
def _e(value):
    return html.escape("" if value is None else str(value))


def _wrap(lang, title, inner_html):
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:0;background:#f4f6fb;font-family:Segoe UI,Arial,sans-serif;">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6fb;padding:24px 0;"><tr><td align="center">'
        '<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:12px;'
        'border:1px solid #e5e7eb;overflow:hidden;">'
        f'<tr><td style="background:#1e2a78;color:#ffffff;padding:20px 28px;font-size:18px;font-weight:700;">{_e(title)}</td></tr>'
        f'<tr><td style="padding:24px 28px;color:#1f2937;font-size:15px;line-height:1.55;">{inner_html}</td></tr>'
        f'<tr><td style="padding:14px 28px;background:#f9fafb;color:#6b7280;font-size:12px;">{_e(tx("em_footer", lang))}</td></tr>'
        '</table></td></tr></table></body></html>'
    )


def _label(value):
    return str(value or "").replace("_", " ").title()


def _type_label(date_type, lang):
    key = f"dt_{str(date_type or '').strip().lower().replace(' ', '_')}"
    value = tx(key, lang)
    return _label(date_type) if value == key else value


def _category_label(category, lang):
    value = tx(str(category or ""), lang) if category else ""
    if not category or value == category:
        return _label(category) if category else tx("uncategorized", lang)
    return value


# ----------------------------------------------------------------------------- date classification
def classify_dates(dates, today=None):
    """Return (rows, expired_count). Each row: dict(type, date, details, days, state, is_deadline)."""
    today = today or date.today()
    rows, expired = [], 0
    for item in dates or []:
        if not isinstance(item, dict):
            try:
                item = dict(item)
            except (TypeError, ValueError):
                continue
        d = to_date(item.get("date_value"))
        if isinstance(d, datetime):
            d = d.date()
        if not isinstance(d, date):
            continue
        deadline = is_deadline(item.get("date_type"), item.get("description"))
        days = (d - today).days
        if not deadline:
            state = "info"
        elif days < 0:
            state, expired = "expired", expired + 1
        elif days == 0:
            state = "today"
        else:
            state = "left"
        rows.append({"type": item.get("date_type"), "date": d, "details": item.get("description") or "",
                     "days": days, "state": state, "is_deadline": deadline})
    rows.sort(key=lambda r: r["date"])
    return rows, expired


def _status_text(row, lang):
    if row["state"] == "expired":
        return tx("em_st_expired", lang, n=abs(row["days"]))
    if row["state"] == "today":
        return tx("em_st_today", lang)
    if row["state"] == "left":
        return tx("em_st_left", lang, n=row["days"])
    return tx("em_st_info", lang)


_TD = "padding:8px 10px;border-bottom:1px solid #eef0f4;font-size:14px;vertical-align:top;"
_TH = "padding:8px 10px;background:#f3f4f8;text-align:left;font-size:13px;color:#374151;"


def _dates_table(rows, lang):
    head = "".join(f'<th style="{_TH}">{_e(tx(k, lang))}</th>' for k in
                   ("col_type", "col_date", "col_details", "col_status"))
    body = ""
    for r in rows:
        color = {"expired": "#b91c1c", "today": "#b91c1c"}.get(r["state"], "#1f2937")
        weight = "700" if r["state"] in ("expired", "today") else "400"
        body += (f'<tr><td style="{_TD}">{_e(_type_label(r["type"], lang))}</td>'
                 f'<td style="{_TD}">{_e(format_date(r["date"]))}</td>'
                 f'<td style="{_TD}">{_e(r["details"])}</td>'
                 f'<td style="{_TD}color:{color};font-weight:{weight};">{_e(_status_text(r, lang))}</td></tr>')
    return (f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="border:1px solid #e5e7eb;border-collapse:collapse;"><tr>{head}</tr>{body}</table>')


# ----------------------------------------------------------------------------- the e-mails
def send_verification_email(to_email, name, code, lang="en"):
    hello = tx("em_hello", lang, name=name)
    body = tx("em_verify_body", lang)
    valid = tx("em_verify_valid", lang, minutes=CODE_VALID_MINUTES)
    inner = (f'<p>{_e(hello)}</p><p>{_e(body)}</p>'
             f'<p style="font-size:34px;font-weight:700;letter-spacing:8px;text-align:center;background:#eef2ff;'
             f'border-radius:10px;padding:14px 0;color:#1e2a78;">{_e(code)}</p><p style="color:#6b7280;">{_e(valid)}</p>')
    text = f"{hello}\n\n{body}\n\n{code}\n\n{valid}\n"
    return _send(to_email, tx("em_verify_subject", lang, code=code), text,
                 _wrap(lang, tx("verify_title", lang), inner))


def send_welcome_email(to_email, name, username, lang="en"):
    hello = tx("em_hello", lang, name=name)
    body = tx("em_welcome_body", lang, username=username)
    nxt = tx("em_welcome_next", lang)
    inner = f'<p>{_e(hello)}</p><p>{_e(body)}</p><p>{_e(nxt)}</p>'
    text = f"{hello}\n\n{body}\n\n{nxt}\n"
    return _send(to_email, tx("em_welcome_subject", lang), text, _wrap(lang, tx("em_welcome_subject", lang), inner))


def send_document_email(to_email, name, doc_name, result, lang="en", today=None):
    """E-mail sent after every uploaded document. Returns (ok, message, expired_count)."""
    result = result or {}
    rows, expired = classify_dates(result.get("dates"), today)
    hello = tx("em_hello", lang, name=name)
    intro = tx("em_upload_body", lang)
    category = _category_label(result.get("category"), lang)
    summary = result.get("summary") or tx("no_summary", lang)

    banner_html = banner_text = ""
    if expired:
        banner_text = tx("em_expired_banner", lang)
        banner_html = (f'<div style="background:#fee2e2;border:1px solid #fca5a5;border-left:6px solid #dc2626;color:#991b1b;'
                       f'padding:12px 14px;border-radius:8px;margin:0 0 16px 0;font-weight:700;">{_e(banner_text)}</div>')

    info = (f'<p><strong>{_e(tx("em_doc_name", lang))}:</strong> {_e(doc_name)}<br>'
            f'<strong>{_e(tx("em_category", lang))}:</strong> {_e(category)}</p>'
            f'<p><strong>{_e(tx("em_summary", lang))}:</strong><br>{_e(summary)}</p>')
    dates_html = (_dates_table(rows, lang) if rows
                  else f'<p style="color:#6b7280;">{_e(tx("em_no_dates", lang))}</p>')
    inner = (banner_html + f'<p>{_e(hello)}</p><p>{_e(intro)}</p>' + info +
             f'<h3 style="margin:18px 0 8px 0;font-size:16px;">{_e(tx("em_dates", lang))}</h3>' + dates_html)

    lines = [hello, "", intro, ""]
    if banner_text:
        lines += [banner_text.upper(), ""]
    lines += [f'{tx("em_doc_name", lang)}: {doc_name}', f'{tx("em_category", lang)}: {category}',
              f'{tx("em_summary", lang)}: {summary}', "", tx("em_dates", lang) + ":"]
    lines += ([f'- {_type_label(r["type"], lang)} | {format_date(r["date"])} | {r["details"]} | {_status_text(r, lang)}'
               for r in rows] or [tx("em_no_dates", lang)])
    subject = (tx("em_upload_subject_expired", lang, doc=doc_name) if expired
               else tx("em_upload_subject", lang, doc=doc_name))
    ok, message = _send(to_email, subject, "\n".join(lines) + "\n", _wrap(lang, subject, inner))
    return ok, message, expired


def send_alert_digest(to_email, name, alerts, lang="en"):
    """Localised digest of deadline alerts (items come from alerts.build_alerts)."""
    if not alerts:
        return False, "There are no alerts to send."
    hello = tx("em_hello", lang, name=name)
    intro = tx("em_alert_intro", lang)
    rows = []
    for a in alerts:
        try:
            days = int(a.get("days_left") or 0)
        except (TypeError, ValueError):
            days = 0
        state = "expired" if days < 0 else ("today" if days == 0 else "left")
        rows.append({"type": a.get("date_type"), "date": a.get("date_value"),
                     "details": f'{a.get("document_name") or ""} {a.get("description") or ""}'.strip(),
                     "days": days, "state": state})
    inner = f'<p>{_e(hello)}</p><p>{_e(intro)}</p>' + _dates_table(rows, lang)
    text = "\n".join([hello, "", intro, ""] +
                     [f'- {_type_label(r["type"], lang)} | {format_date(r["date"])} | {r["details"]} | {_status_text(r, lang)}'
                      for r in rows]) + "\n"
    subject = tx("em_alert_subject", lang, n=len(rows))
    return _send(to_email, subject, text, _wrap(lang, subject, inner))
