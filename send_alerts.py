"""Send deadline reminder e-mails to every user. Run daily from cron / Task Scheduler:

    python send_alerts.py            # deadlines within 30 days (overdue ones are not re-sent)
    python send_alerts.py --days 14
"""
import argparse
from collections import defaultdict
from datetime import date

from alerts import build_alerts
from database import Database
from email_handler import EmailHandler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    db, mailer = Database(), EmailHandler()
    problems = mailer.config_problems()
    if problems:
        raise SystemExit("E-mail is not configured: " + "; ".join(problems))
    rows = db.get_pending_alerts(args.days)
    if rows is None:
        raise SystemExit(f"Database error: {db.last_error}")

    per_user = defaultdict(list)
    for row in rows:
        per_user[(row["user_id"], row["email"], row["full_name"])].append(row)

    sent = failed = 0
    for (_, email, name), items in per_user.items():
        alerts = build_alerts(items, today=date.today(), horizon=args.days, include_expired=False)
        if not alerts:
            continue
        ok, message = mailer.send_expiry_alert(email, name, alerts)
        print(f"{email}: {message}")
        if ok:
            sent += 1
            for a in alerts:
                db.mark_alert_sent(a["id"])
        else:
            failed += 1
    print(f"Done. Users e-mailed: {sent}, failed: {failed}.")


if __name__ == "__main__":
    main()
