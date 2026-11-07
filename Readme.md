# SME Compliance & Document Intelligence Platform

Streamlit + MySQL app: upload documents (PDF / images / DOCX), AI or rule-based analysis, key-date extraction,
deadline alerts by e-mail, semantic search, English / Hindi / Kannada UI.

## Run

```bash
python -m venv venv
venv\Scripts\activate            # Windows   (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt
# put your .env next to app.py (see .env.example for all variable names)
streamlit run app.py
```

The database and all tables are created automatically on first start (from `database.sql`).
The MySQL user in `.env` needs CREATE privileges, or create the empty database yourself first.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI |
| `database.py` / `database.sql` | MySQL access (connection pool, bcrypt passwords) and schema |
| `document_processor.py` | Text extraction, OCR, AI + rule-based analysis, search |
| `alerts.py` | Deadline logic (levels, dates) |
| `notifications.py` | Localised e-mails (verification, welcome, document, alert digest) |
| `email_handler.py` | SMTP sending, connection test |
| `send_alerts.py` | Daily reminder job: `python send_alerts.py --days 30` (cron / Task Scheduler) |
| `translations.py` | All UI + e-mail strings |
| `.streamlit/config.toml` | Theme and upload limit |
