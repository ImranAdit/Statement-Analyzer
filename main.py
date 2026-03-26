from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import pdfplumber
import pytesseract
from PIL import Image
import io
import re
import json
import os
from typing import Optional

app = FastAPI(title="Adit Pay Statement Analyser")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Calculation Logic (mirrors the spreadsheet) ──────────────────────────────

ADIT_RATE_CARD_PRESENT   = 0.0225   # 2.25%
ADIT_AUTH_CARD_PRESENT   = 0.20     # $0.20 per trn
ADIT_RATE_ONLINE         = 0.0290   # 2.90%
ADIT_AUTH_ONLINE         = 0.30     # $0.30 per trn


def calc_adit_card_present(amount: float, count: int) -> dict:
    trn_fee   = amount * ADIT_RATE_CARD_PRESENT
    auth_fee  = count  * ADIT_AUTH_CARD_PRESENT
    total_fee = trn_fee + auth_fee
    return {
        "type": "Card Present",
        "amount": amount,
        "count": count,
        "trn_fee": round(trn_fee, 2),
        "auth_fee": round(auth_fee, 2),
        "total_fee": round(total_fee, 2),
        "rate_label": "2.25% + $0.20",
    }


def calc_adit_online(amount: float, count: int) -> dict:
    trn_fee   = amount * ADIT_RATE_ONLINE
    auth_fee  = count  * ADIT_AUTH_ONLINE
    total_fee = trn_fee + auth_fee
    return {
        "type": "Online (Card Not Present)",
        "amount": amount,
        "count": count,
        "trn_fee": round(trn_fee, 2),
        "auth_fee": round(auth_fee, 2),
        "total_fee": round(total_fee, 2),
        "rate_label": "2.90% + $0.30",
    }


def build_analysis(
    existing_merchant: str,
    total_amount: float,
    total_count: int,
    total_fees_paid: float,
    card_present_pct: float,   # 0-1
    mode: str = "template",    # "template" | "card_present_only"
):
    if mode == "card_present_only":
        # Card-present-only sheet logic
        adit = calc_adit_card_present(total_amount, total_count)
        adit_total = adit["total_fee"]
        rows = [adit]
        avg_fee_pct = adit_total / total_amount if total_amount else 0
    else:
        # Template (split) logic
        online_pct = 1.0 - card_present_pct
        cp_amount  = total_amount * card_present_pct
        cp_count   = total_count  * card_present_pct
        on_amount  = total_amount * online_pct
        on_count   = total_count  * online_pct

        adit_cp    = calc_adit_card_present(cp_amount, cp_count)
        adit_on    = calc_adit_online(on_amount, on_count)
        adit_total = adit_cp["total_fee"] + adit_on["total_fee"]
        rows = [adit_cp, adit_on]
        avg_fee_pct = adit_total / total_amount if total_amount else 0

    savings          = total_fees_paid - adit_total
    existing_avg_pct = total_fees_paid / total_amount if total_amount else 0

    return {
        "existing_merchant":   existing_merchant,
        "total_amount":        round(total_amount, 2),
        "total_count":         total_count,
        "total_fees_paid":     round(total_fees_paid, 2),
        "existing_avg_fee_pct":round(existing_avg_pct * 100, 4),
        "card_present_pct":    round(card_present_pct * 100, 1),
        "online_pct":          round((1 - card_present_pct) * 100, 1),
        "mode":                mode,
        "adit_rows":           rows,
        "adit_total_fee":      round(adit_total, 2),
        "adit_avg_fee_pct":    round(avg_fee_pct * 100, 4),
        "savings":             round(savings, 2),
    }


# ── PDF / Image Extraction ────────────────────────────────────────────────────

def extract_text_from_pdf(data: bytes) -> str:
    text = ""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text


def extract_text_from_image(data: bytes) -> str:
    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img)


def parse_currency(s: str) -> Optional[float]:
    s = re.sub(r"[,$\s]", "", s)
    try:
        return float(s)
    except Exception:
        return None


def parse_statement(raw_text: str) -> dict:
    """Best-effort extraction of key figures from a bank/merchant statement."""
    text = raw_text.replace(",", "").lower()

    # Try to find total transaction amount
    total_amount = None
    for pattern in [
        r"total\s+(?:trn|transaction|sale|sales|gross)\s+(?:amt|amount)[^\d]*(\d+\.?\d*)",
        r"gross\s+sales[^\d]*(\d+\.?\d*)",
        r"total\s+sales[^\d]*(\d+\.?\d*)",
        r"total\s+amount[^\d]*(\d+\.?\d*)",
        r"net\s+sales[^\d]*(\d+\.?\d*)",
    ]:
        m = re.search(pattern, text)
        if m:
            total_amount = float(m.group(1))
            break

    # Count
    total_count = None
    for pattern in [
        r"(?:no|number|num)\s+(?:of\s+)?(?:trn|transaction|sale)[^\d]*(\d+)",
        r"(?:trn|transaction)\s+count[^\d]*(\d+)",
        r"total\s+(?:trn|transaction)[^\d]*(\d+)",
    ]:
        m = re.search(pattern, text)
        if m:
            total_count = int(m.group(1))
            break

    # Fees
    total_fees = None
    for pattern in [
        r"total\s+(?:fees?|fee\s+paid|trn\s+fee)[^\d]*(\d+\.?\d*)",
        r"processing\s+fee[^\d]*(\d+\.?\d*)",
        r"merchant\s+(?:service\s+)?fee[^\d]*(\d+\.?\d*)",
    ]:
        m = re.search(pattern, text)
        if m:
            total_fees = float(m.group(1))
            break

    # Merchant name
    merchant = "Unknown"
    for pattern in [
        r"merchant\s*(?:name)?[:\s]+([A-Za-z0-9 &.'-]+)",
        r"(?:dba|doing business as)[:\s]+([A-Za-z0-9 &.'-]+)",
    ]:
        m = re.search(pattern, raw_text, re.IGNORECASE)
        if m:
            merchant = m.group(1).strip()[:40]
            break

    return {
        "merchant":    merchant,
        "total_amount": total_amount,
        "total_count":  total_count,
        "total_fees":   total_fees,
        "raw_text":     raw_text[:3000],   # send a snippet to help manual review
    }


# ── Pydantic models ───────────────────────────────────────────────────────────

class ManualInput(BaseModel):
    existing_merchant:  str
    total_amount:       float
    total_count:        int
    total_fees_paid:    float
    card_present_pct:   float   # 0-100
    mode:               str = "template"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_statement(file: UploadFile = File(...)):
    data = await file.read()
    content_type = file.content_type or ""
    fname = (file.filename or "").lower()

    try:
        if "pdf" in content_type or fname.endswith(".pdf"):
            raw = extract_text_from_pdf(data)
        elif any(fname.endswith(e) for e in [".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"]):
            raw = extract_text_from_image(data)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type. Upload a PDF or image.")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read file: {e}")

    extracted = parse_statement(raw)
    return {
        "extracted": extracted,
        "message":   "Review and adjust the extracted values below before calculating.",
    }


@app.post("/api/calculate")
async def calculate(inp: ManualInput):
    try:
        result = build_analysis(
            existing_merchant=inp.existing_merchant,
            total_amount=inp.total_amount,
            total_count=inp.total_count,
            total_fees_paid=inp.total_fees_paid,
            card_present_pct=inp.card_present_pct / 100.0,
            mode=inp.mode,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


# ── Serve React frontend ──────────────────────────────────────────────────────
FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")

if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
