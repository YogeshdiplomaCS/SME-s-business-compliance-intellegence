"""alerts.py -- deadline logic (no e-mail code here; see notifications.py).

Public API: LEVEL_ORDER, to_date, format_date, is_deadline, level_for, build_alerts
"""
import re
from datetime import date, datetime

LEVEL_ORDER = ["expired", "critical", "warning", "upcoming"]
ALERT_WINDOW_DAYS = 30      # show deadlines up to this many days ahead (overdue ones are always shown)
CRITICAL_DAYS = 7           # 0..7 days left   -> critical
WARNING_DAYS = 15           # 8..15 days left  -> warning, later -> upcoming

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y",
                 "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y")
_DEADLINE_TYPE_WORDS = {"expiry", "expire", "expires", "expired", "expiration", "renewal", "renew", "due",
                        "deadline", "end", "termination", "terminate", "maturity", "validity", "valid", "until"}
_INFO_TYPE_WORDS = {"issue", "issued", "start", "begin", "invoice", "effective", "signed", "signing", "created",
                    "birth", "dob"}
_DEADLINE_DESC_RE = re.compile(r"\b(expir\w*|renew\w*|due|deadline|valid until|valid till)\b", re.I)


def to_date(value):
    """Convert date / datetime / pandas Timestamp / common date strings to datetime.date (or None)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):           # pandas Timestamp
        try:
            return value.to_pydatetime().date()
        except (ValueError, AttributeError):
            return None
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text[:10]):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def format_date(value):
    """Display format YYYY-MM-DD; unknown values are shown as given ('N/A' when empty)."""
    if value is None or value == "":
        return "N/A"
    parsed = to_date(value)
    return parsed.strftime("%Y-%m-%d") if parsed else str(value)


def is_deadline(date_type, description=""):
    """True for dates that can expire (expiry, renewal, due, end ...); False for informational dates."""
    words = set(re.split(r"[^a-z]+", str(date_type or "").lower())) - {""}
    if words & _DEADLINE_TYPE_WORDS:
        return True
    if words & _INFO_TYPE_WORDS:
        return False
    return bool(_DEADLINE_DESC_RE.search(str(description or "")))


def level_for(days_left):
    if days_left < 0:
        return "expired"
    if days_left <= CRITICAL_DAYS:
        return "critical"
    if days_left <= WARNING_DAYS:
        return "warning"
    return "upcoming"


def _as_dict(item):
    if isinstance(item, dict):
        return item
    try:
        return dict(item)
    except (TypeError, ValueError):
        return None


def build_alerts(rows, today=None, window=ALERT_WINDOW_DAYS, horizon=None, include_expired=True):
    """Turn important-date rows into alerts, sorted by urgency.

    window / horizon : show deadlines up to this many days ahead (horizon overrides window)
    include_expired  : False drops deadlines that are already in the past

    Every alert keeps the keys of its row (id, document_name, date_type, description ...) and gains
    `date_value` (a date), `days_left` (negative = overdue) and `level` (one of LEVEL_ORDER).
    """
    today = today or date.today()
    limit = window if horizon is None else horizon
    alerts = []
    for item in rows or []:
        row = _as_dict(item)
        if row is None:
            continue
        due = to_date(row.get("date_value"))
        if due is None or not is_deadline(row.get("date_type"), row.get("description")):
            continue
        days_left = (due - today).days
        if days_left > limit or (days_left < 0 and not include_expired):
            continue
        alert = dict(row)
        alert["date_value"] = due
        alert["days_left"] = days_left
        alert["level"] = level_for(days_left)
        if alert.get("id") is None and row.get("alert_id") is not None:
            alert["id"] = row["alert_id"]
        if not alert.get("document_name"):
            alert["document_name"] = row.get("name") or row.get("filename") or ""
        alerts.append(alert)
    alerts.sort(key=lambda a: (a["days_left"], str(a.get("document_name") or "")))
    return alerts
