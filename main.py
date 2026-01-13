from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from app.sheets import get_sheet, append_checkin
from datetime import datetime
import qrcode
import io
import uuid
import threading
import time
from urllib.parse import quote
from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ─────────────── CONFIG ───────────────
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
QR_REFRESH_SECONDS = 20  # not really needed for stress test
BASE_URL_FALLBACK = os.getenv("BASE_URL", "http://localhost:8000")

# ─────────────── GLOBAL STATE ───────────────
used_devices = set()
pending_checkins = []

lock = threading.Lock()

# ─────────────── MODELS ───────────────
class CheckinData(BaseModel):
    ime: str
    jmbag: str
    device_id: str
    token: Optional[str] = None  # ignored for now

# ─────────────── STUDENT FORM ───────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/form", response_class=HTMLResponse)
async def show_form(request: Request):
    """
    Renders the student check-in form.
    Token is ignored in this stress-test version.
    """
    base_url = str(request.base_url).rstrip('/')
    return templates.TemplateResponse("checkin.html", {
        "request": request,
        "token": "",  # token ignored
        "base_url": base_url
    })

# ─────────────── CHECK-IN ENDPOINT ───────────────
@app.post("/checkin")
async def checkin(data: CheckinData):
    with lock:
        # Device already used
        if data.device_id in used_devices:
            raise HTTPException(400, "Ovaj uređaj je već prijavljen u ovoj sesiji.")

        # Token check skipped for stress test

        used_devices.add(data.device_id)
        pending_checkins.append(data.dict())

    return {"status": "success"}

# ─────────────── ADMIN DASHBOARD ───────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, password: str = None):
    if password != ADMIN_PASSWORD:
        return HTMLResponse("""
        <form method="get">
            <h2>Admin pristup</h2>
            Lozinka: <input type="password" name="password">
            <button type="submit">Prijava</button>
        </form>
        """)

    with lock:
        device_count = len(used_devices)

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "device_count": device_count,
        "qr_url": "/qr",
        "password": ADMIN_PASSWORD
    })

@app.post("/admin/reset")
async def reset_session(password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        raise HTTPException(403, "Pogrešna lozinka")

    with lock:
        used_devices.clear()
        pending_checkins.clear()

    return {"status": "Sesija resetirana"}

# ─────────────── BATCH WRITER THREAD ───────────────
def batch_writer():
    while True:
        time.sleep(2)
        with lock:
            batch = pending_checkins.copy()
            pending_checkins.clear()

        if not batch:
            continue

        try:
            sheet = get_sheet()
            rows = [[item["ime"], item["jmbag"]] for item in batch]
            sheet.append_rows(rows)
            print(f"Batch success: {len(rows)} rows written")
        except Exception as e:
            print(f"Batch write failed: {str(e)}")
            with lock:
                pending_checkins.extend(batch)

threading.Thread(target=batch_writer, daemon=True).start()
