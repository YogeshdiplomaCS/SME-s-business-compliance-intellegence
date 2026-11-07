"""Document text extraction, AI analysis (OpenRouter) and a rule-based fallback."""
import base64
import io
import json
import logging
import os
import re
import time
from datetime import date, datetime

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        PdfReader = None

try:
    import docx
except ImportError:  # pragma: no cover
    docx = None

try:
    import fitz  # PyMuPDF, used only to OCR scanned PDFs
except ImportError:  # pragma: no cover
    fitz = None

load_dotenv()
log = logging.getLogger("document_processor")

CATEGORIES = ("invoice", "contract", "license", "certificate", "purchase_order", "agreement", "policy", "other")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
MAX_ANALYSIS_CHARS = 15000

CATEGORY_KEYWORDS = {
    "invoice": ["invoice", "tax invoice", "bill to", "gstin", "amount due", "total amount", "receipt", "bill no"],
    "purchase_order": ["purchase order", "po number", "po no", "p.o.", "ship to"],
    "contract": ["contract", "hereinafter", "terms and conditions", "witnesseth", "first party", "second party"],
    "agreement": ["agreement", "memorandum of understanding", "mou", "settlement", "undertaking"],
    "license": ["licence", "license", "permit", "registration no", "valid until", "authorised to", "trade licence"],
    "certificate": ["certificate", "this is to certify", "certification", "awarded", "hereby certified"],
    "policy": ["policy", "procedure", "guidelines", "code of conduct", "compliance manual"],
}

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})\.?,?[\s\-]+(\d{4})\b"), "d_mon_y"),
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"), "mon_d_y"),
]
_DATE_TYPE_PATTERNS = [
    ("expiry_date", r"expir|valid\s*(?:until|upto|up to|till|through|thru|to)\b|validity"),
    ("renewal_date", r"renew"),
    ("due_date", r"\bdue\b"),
    ("end_date", r"terminat|end date|ends? on"),
    ("start_date", r"effective|commenc|start|begin|valid from"),
    ("issue_date", r"issue"),
    ("signature_date", r"\bsign"),
    ("document_date", r"invoice date|dated|\bdate\b"),
]
_FIELD_PATTERNS = {
    "invoice_number": r"(?:invoice|inv|bill)\s*(?:no|number|#)\.?\s*[:\-#]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{2,})",
    "po_number": r"(?:purchase order|p\.?o\.?)\s*(?:no|number|#)\.?\s*[:\-#]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{2,})",
    "license_number": r"(?:licen[cs]e|registration|certificate|reg)\s*(?:no|number|#)\.?\s*[:\-#]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{2,})",
    "gstin": r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b",
    "pan": r"\b([A-Z]{5}\d{4}[A-Z])\b",
    "total_amount": r"(?:grand\s+total|total\s+amount|amount\s+due|net\s+payable|total)[^\d]{0,25}([\d,]+(?:\.\d{1,2})?)",
    "email": r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b",
}


class LLMError(Exception):
    pass


def _make_date(y, m, d):
    try:
        value = date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None
    return value if 1990 <= value.year <= 2100 else None


def _month_number(word):
    word = word.lower()
    if len(word) < 3:
        return None
    for i, name in enumerate(_MONTHS, 1):
        if name.startswith(word):
            return i
    return None


def find_dates(text):
    """Rule-based date finder. Returns [{date_type, date_value, description}]."""
    matches, spans = [], []
    for pattern, kind in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < e and m.end() > s for s, e in spans):
                continue
            a, b, c = m.groups()
            value = None
            if kind == "ymd":
                value = _make_date(a, b, c)
            elif kind == "dmy":
                value = _make_date(c, b, a)
                if (value is None or int(b) > 12) and int(a) <= 12:
                    value = _make_date(c, a, b)  # month-first when the second number cannot be a month
            elif kind == "d_mon_y":
                mon = _month_number(b)
                value = _make_date(c, mon, a) if mon else None
            elif kind == "mon_d_y":
                mon = _month_number(a)
                value = _make_date(c, mon, b) if mon else None
            if value:
                spans.append((m.start(), m.end()))
                matches.append((m.start(), m.end(), value))
    matches.sort()

    found, prev_end, prev_type = [], -1, None
    for start, end, value in matches:
        line_start = text.rfind("\n", 0, start) + 1
        same_line_prev = prev_end >= line_start
        ctx_start = max(line_start, start - 60, prev_end if same_line_prev else 0)
        context = text[ctx_start:start].lower()
        dtype = "document_date"
        for name, pat in _DATE_TYPE_PATTERNS:  # list order = priority
            if re.search(pat, context):
                dtype = name
                break
        else:
            if same_line_prev and prev_type == "start_date" and re.search(r"\bto\b|until|till|-|through", context):
                dtype = "end_date"  # "valid from A to B"
        label = " ".join(context.split())[-40:].strip(" :-") or dtype.replace("_", " ")
        found.append({"date_type": dtype, "date_value": value.isoformat(), "description": f"Found near: {label}"})
        prev_end, prev_type = end, dtype
    return found


def find_fields(text):
    fields = {}
    for name, pattern in _FIELD_PATTERNS.items():
        flags = 0 if name in ("gstin", "pan") else re.IGNORECASE
        m = re.search(pattern, text, flags)
        if m:
            fields[name] = m.group(1).strip().rstrip(".,")
    return fields


def guess_category(text, filename=""):
    haystack = text.lower()
    name = filename.lower()
    scores = {}
    for cat, words in CATEGORY_KEYWORDS.items():
        score = sum(haystack.count(w) for w in words) + 3 * sum(1 for w in words if w in name)
        if score:
            scores[cat] = score
    if not scores:
        return "other", 0.3, "No category keywords were found."
    cat = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = round(min(0.9, 0.4 + 0.5 * scores[cat] / total), 2)
    return cat, confidence, f"Matched keywords typical of a {cat.replace('_', ' ')}."


class DocumentProcessor:
    def __init__(self):
        self.api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        primary = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001").strip()
        fallbacks = [m.strip() for m in os.getenv("OPENROUTER_FALLBACK_MODELS", "google/gemini-2.5-flash").split(",") if m.strip()]
        vision = os.getenv("OPENROUTER_VISION_MODEL", primary).strip()
        self.text_models = list(dict.fromkeys([primary] + fallbacks))
        self.vision_models = list(dict.fromkeys([vision] + fallbacks))
        self.ocr_max_pages = int(os.getenv("OCR_MAX_PAGES", 5))
        self.uploads_folder = os.getenv("UPLOAD_FOLDER", "uploads")
        self.processed_folder = os.getenv("PROCESSED_FOLDER", "processed")
        os.makedirs(self.uploads_folder, exist_ok=True)
        os.makedirs(self.processed_folder, exist_ok=True)

    # ------------------------------------------------------------- LLM access
    def ai_available(self):
        return bool(self.api_key)

    def _chat(self, models, messages, max_tokens=2500):
        if not self.api_key:
            raise LLMError("OPENROUTER_API_KEY is not set")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("APP_URL", "http://localhost:8501"),
            "X-Title": "SME Document Intelligence",
        }
        errors = []
        for model in models:
            for attempt in range(2):
                try:
                    resp = requests.post(
                        self.api_url, headers=headers, timeout=(10, 90),
                        json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.1})
                except requests.RequestException as exc:
                    errors.append(f"{model}: network error ({exc.__class__.__name__})")
                    time.sleep(1)
                    continue
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        content = data["choices"][0]["message"]["content"]
                    except (ValueError, KeyError, IndexError, TypeError):
                        detail = ""
                        try:
                            detail = str(resp.json().get("error", ""))[:150]
                        except Exception:
                            pass
                        errors.append(f"{model}: unexpected response {detail}".strip())
                        break
                    if isinstance(content, list):
                        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                    if content and content.strip():
                        return content
                    errors.append(f"{model}: empty response")
                    break
                if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(2)
                    continue
                errors.append(f"{model}: HTTP {resp.status_code} {resp.text[:120].strip()}")
                break
        raise LLMError("; ".join(errors) or "No model responded")

    @staticmethod
    def _parse_json(text):
        text = re.sub(r"```(?:json)?", "", text or "").strip()
        try:
            return json.loads(text)
        except ValueError:
            pass
        for open_c, close_c in (("{", "}"), ("[", "]")):
            start, end = text.find(open_c), text.rfind(close_c)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except ValueError:
                    continue
        raise ValueError("The model did not return valid JSON")

    def check_api(self):
        """Quick health check used by the Settings page."""
        try:
            self._chat(self.text_models, [{"role": "user", "content": "Reply with the single word OK."}], max_tokens=10)
            return True, f"AI service reachable (model: {self.text_models[0]})."
        except LLMError as exc:
            return False, str(exc)

    # ------------------------------------------------------- text extraction
    @staticmethod
    def _image_to_jpeg_bytes(img):
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((2000, 2000))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return buf.getvalue()

    def _ocr_bytes(self, jpeg_bytes):
        b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
        prompt = ("You are an OCR system. Transcribe ALL text in this document image exactly, keeping numbers, "
                  "dates and line order. Return only the transcribed text.")
        messages = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt}]}]
        return self._chat(self.vision_models, messages, max_tokens=4000)

    def _text_from_image(self, path):
        texts = []
        with Image.open(path) as img:
            frames = getattr(img, "n_frames", 1)
            for i in range(min(frames, self.ocr_max_pages)):
                img.seek(i)
                texts.append(self._ocr_bytes(self._image_to_jpeg_bytes(img.copy())))
        return "\n\n".join(texts)

    def _text_from_pdf(self, path):
        text, warning = "", None
        if PdfReader is None:
            warning = "Install pypdf to read PDF text."
        else:
            try:
                reader = PdfReader(path)
                if getattr(reader, "is_encrypted", False):
                    try:
                        reader.decrypt("")
                    except Exception:
                        return "", "The PDF is password protected."
                text = "\n".join((page.extract_text() or "") for page in reader.pages)
            except Exception as exc:
                warning = f"PDF could not be read ({exc.__class__.__name__})."
        if len(text.strip()) >= 40:
            return text, warning
        # Scanned PDF: render pages and OCR them
        if fitz is None:
            return text, (warning or "") + " This looks like a scanned PDF; install PyMuPDF (pip install pymupdf) to enable OCR."
        if not self.api_key:
            return text, "This looks like a scanned PDF; set OPENROUTER_API_KEY to enable OCR."
        pages = []
        with fitz.open(path) as pdf:
            for i in range(min(len(pdf), self.ocr_max_pages)):
                pix = pdf[i].get_pixmap(matrix=fitz.Matrix(2, 2))
                with Image.open(io.BytesIO(pix.tobytes("png"))) as img:
                    pages.append(self._ocr_bytes(self._image_to_jpeg_bytes(img)))
        return "\n\n".join(pages), None

    @staticmethod
    def _text_from_docx(path):
        if docx is None:
            return "", "Install python-docx to read Word files."
        document = docx.Document(path)
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts), None

    def extract_text(self, file_path, ext):
        """Returns (text, warning)."""
        ext = ext.lower()
        if ext == ".pdf":
            return self._text_from_pdf(file_path)
        if ext == ".docx":
            return self._text_from_docx(file_path)
        if ext in IMAGE_EXTS:
            if not self.api_key:
                return "", "Images need OCR: set OPENROUTER_API_KEY in your .env file."
            return self._text_from_image(file_path), None
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(), None

    # --------------------------------------------------------------- analysis
    def _analyze_with_ai(self, text, filename):
        prompt = f"""You analyse business and compliance documents for small companies.
Today's date is {date.today().isoformat()}.
Filename: {filename}

Document text:
\"\"\"
{text[:MAX_ANALYSIS_CHARS]}
\"\"\"

Return ONLY one JSON object (no markdown) with exactly these keys:
{{
  "category": one of {list(CATEGORIES)},
  "confidence": number 0-1,
  "reason": "one short sentence",
  "summary": "2-3 sentence plain-language summary of the document",
  "dates": [{{"date_type": "expiry_date|renewal_date|due_date|issue_date|start_date|end_date|signature_date|document_date",
              "date_value": "YYYY-MM-DD", "description": "what the date means"}}],
  "fields": {{"snake_case_field_name": "value as text"}}
}}
Rules: include only dates that literally appear in the text; convert them to YYYY-MM-DD (day-first for dd/mm/yyyy);
fields should hold identifiers, parties, amounts and licence/registration numbers found in the text (flat string values);
use an empty list / object if nothing is found. Never invent data."""
        raw = self._chat(self.text_models, [{"role": "user", "content": prompt}])
        data = self._parse_json(raw)
        if not isinstance(data, dict):
            raise ValueError("Unexpected JSON structure")
        return data

    @staticmethod
    def _clean_dates(items):
        clean, seen = [], set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            value = None
            raw = str(item.get("date_value", "")).strip()[:10]
            try:
                value = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                continue
            if not 1990 <= value.year <= 2100:
                continue
            dtype = re.sub(r"[^a-z_]", "_", str(item.get("date_type", "document_date")).lower()).strip("_") or "document_date"
            key = (dtype, value)
            if key in seen:
                continue
            seen.add(key)
            clean.append({"date_type": dtype, "date_value": value.isoformat(),
                          "description": str(item.get("description", ""))[:300]})
        return clean

    def analyze(self, text, filename):
        """Return a result dict. Uses AI when available, otherwise rules."""
        rule_cat, rule_conf, rule_reason = guess_category(text, filename)
        rule_dates = find_dates(text)
        rule_fields = find_fields(text)
        warning = None
        ai = None
        if self.api_key:
            try:
                ai = self._analyze_with_ai(text, filename)
            except (LLMError, ValueError) as exc:
                warning = f"AI analysis was unavailable, so rule-based analysis was used. Reason: {exc}"
        else:
            warning = "OPENROUTER_API_KEY is not set, so rule-based analysis was used."

        if ai:
            category = str(ai.get("category", "")).strip().lower().replace(" ", "_")
            if category not in CATEGORIES:
                category = rule_cat
            try:
                confidence = max(0.0, min(1.0, float(ai.get("confidence", 0.7))))
            except (TypeError, ValueError):
                confidence = 0.7
            dates = self._clean_dates(ai.get("dates"))
            known = {(d["date_type"], d["date_value"]) for d in dates}
            known_values = {d["date_value"] for d in dates}
            for d in self._clean_dates(rule_dates):
                if d["date_value"] not in known_values and (d["date_type"], d["date_value"]) not in known:
                    dates.append(d)  # add dates the model missed
            fields = ai.get("fields") if isinstance(ai.get("fields"), dict) else {}
            fields = {str(k): v for k, v in fields.items() if v not in (None, "", [], {})}
            for k, v in rule_fields.items():
                fields.setdefault(k, v)
            return {"category": category, "confidence": confidence,
                    "reason": str(ai.get("reason", ""))[:300], "summary": str(ai.get("summary", ""))[:1000],
                    "dates": dates, "fields": fields, "method": "ai", "warning": None}

        first_line = next((ln.strip() for ln in text.splitlines() if len(ln.strip()) > 8), "")
        return {"category": rule_cat, "confidence": rule_conf, "reason": rule_reason,
                "summary": f"Detected as {rule_cat.replace('_', ' ')}. {first_line[:200]}".strip(),
                "dates": self._clean_dates(rule_dates), "fields": rule_fields,
                "method": "rule_based", "warning": warning}

    # --------------------------------------------------------------- pipeline
    def _store_result(self, doc_id, text, result, db):
        db.clear_analysis(doc_id)
        for d in result["dates"]:
            db.save_important_date(doc_id, d["date_type"], d["date_value"], d["description"])
        conf = result["confidence"]
        for name, value in result["fields"].items():
            db.save_extracted_info(doc_id, name, value, conf)
        if result.get("summary"):
            db.save_extracted_info(doc_id, "summary", result["summary"], conf)
        metadata = {"categorization": {"category": result["category"], "confidence": conf, "reason": result["reason"]},
                    "summary": result.get("summary", ""), "analysis_method": result["method"],
                    "warning": result.get("warning"), "dates_count": len(result["dates"]),
                    "analyzed_at": datetime.now().isoformat(timespec="seconds")}
        db.update_document(doc_id, category=result["category"], extracted_text=text,
                           metadata=json.dumps(metadata), status="completed")

    def _run(self, doc_id, file_path, filename, db, text=None):
        db.update_document(doc_id, status="processing")
        try:
            warning = None
            if not text:
                ext = os.path.splitext(filename)[1].lower()
                if not os.path.exists(file_path):
                    db.update_document(doc_id, status="failed")
                    return doc_id, "The stored file could not be found on disk.", None
                text, warning = self.extract_text(file_path, ext)
            text = (text or "").strip()
            if not text:
                db.update_document(doc_id, status="failed")
                reason = warning or "No readable text was found in the file."
                return doc_id, f"Analysis failed. {reason}".strip(), None
            result = self.analyze(text, filename)
            if warning and not result.get("warning"):
                result["warning"] = warning
            self._store_result(doc_id, text, result, db)
            message = (f"Analysis complete: {result['category'].replace('_', ' ')}, "
                       f"{len(result['dates'])} date(s), {len(result['fields'])} field(s) found.")
            return doc_id, message, result
        except Exception as exc:  # keep the app alive and report the cause
            log.exception("Processing failed")
            db.update_document(doc_id, status="failed")
            return doc_id, f"Analysis failed: {exc.__class__.__name__}: {exc}", None

    def process_document(self, file_path, filename, user_id, db):
        """Save, extract, analyse and store. Returns (doc_id, message, result_or_None)."""
        ext = os.path.splitext(filename)[1].lower()
        doc_id = db.save_document(user_id, filename, ext, file_path, ext)
        if not doc_id:
            return None, f"Could not save the document record. {db.last_error or ''}".strip(), None
        return self._run(doc_id, file_path, filename, db)

    def reanalyze(self, doc_id, user_id, db):
        doc = db.get_document_by_id(doc_id, user_id)
        if not doc:
            return None, "Document not found.", None
        return self._run(doc_id, doc.get("file_path"), doc["document_name"], db, text=doc.get("extracted_text"))

    # ----------------------------------------------------------------- search
    @staticmethod
    def _snippet(text, tokens, width=220):
        low = text.lower()
        pos = min((low.find(t) for t in tokens if t in low), default=-1)
        if pos < 0:
            return " ".join(text[:width].split())
        start = max(0, pos - width // 3)
        return " ".join(text[start:start + width].split())

    def keyword_search(self, query, documents):
        tokens = [t for t in re.split(r"[\s,;:.!?()\[\]{}\"']+", query.lower()) if len(t) >= 2]
        if not tokens:
            return []
        results = []
        for doc in documents:
            name = (doc.get("document_name") or "").lower()
            cat = (doc.get("category") or "").lower()
            text = doc.get("extracted_text") or ""
            low = text.lower()
            score = 0
            for t in tokens:
                score += (3 if t in name else 0) + (2 if t in cat else 0) + min(low.count(t), 5)
            if score:
                results.append({"document_id": doc["id"], "document_name": doc.get("document_name"),
                                "category": doc.get("category"),
                                "relevance_score": min(1.0, score / (len(tokens) * 6)),
                                "snippet": self._snippet(text, tokens), "method": "keyword",
                                "reason": "Matched: " + ", ".join(t for t in tokens if t in name or t in cat or t in low)})
        return sorted(results, key=lambda r: r["relevance_score"], reverse=True)

    def semantic_search(self, query, documents, language="en"):
        docs = [d for d in documents if d.get("extracted_text")][:30]
        if not docs:
            return []
        by_id = {d["id"]: d for d in docs}
        if self.api_key:
            try:
                prompt = (f'You are a semantic search assistant for business documents.\nUser query (language code {language}): "{query}"\n\n'
                          "Documents:\n")
                for d in docs:
                    prompt += (f"\nID: {d['id']}\nName: {d['document_name']}\nCategory: {d.get('category')}\n"
                               f"Preview: {d['extracted_text'][:700]}\n")
                prompt += ('\nReturn ONLY a JSON array (no markdown) of relevant documents, best first:\n'
                           '[{"document_id": <id>, "relevance_score": <0-1>, "snippet": "<short relevant excerpt>", '
                           '"reason": "<why it matches>"}]\nOnly include relevance_score > 0.3. Use [] if nothing matches.')
                data = self._parse_json(self._chat(self.text_models, [{"role": "user", "content": prompt}], max_tokens=1500))
                results = []
                for item in data if isinstance(data, list) else []:
                    try:
                        doc = by_id.get(int(item.get("document_id")))
                        score = float(item.get("relevance_score", 0))
                    except (TypeError, ValueError):
                        continue
                    if doc and score > 0.3:
                        results.append({"document_id": doc["id"], "document_name": doc["document_name"],
                                        "category": doc.get("category"), "relevance_score": min(score, 1.0),
                                        "snippet": str(item.get("snippet", ""))[:400], "method": "semantic",
                                        "reason": str(item.get("reason", ""))[:300]})
                if results:
                    return sorted(results, key=lambda r: r["relevance_score"], reverse=True)
            except (LLMError, ValueError) as exc:
                log.warning("Semantic search fell back to keywords: %s", exc)
        return self.keyword_search(query, docs)


if __name__ == "__main__":
    DocumentProcessor()
    print("Document Processor initialized successfully!")
