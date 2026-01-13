from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from app.sheets import get_sheet, append_checkin  # we still use append_checkin but simplified
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
import json

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ─────────────── CONFIG ───────────────
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
QR_REFRESH_SECONDS = 30000
BASE_URL_FALLBACK = os.getenv("BASE_URL", "http://localhost:8000")

# ─────────────── GLOBAL STATE ───────────────
current_qr_token: Optional[str] = None
previous_qr_token: Optional[str] = None
used_devices = set()
pending_checkins = []

lock = threading.Lock()

# ─────────────── BACKGROUND QR ROTATION ───────────────
def rotate_token():
    global current_qr_token, previous_qr_token
    while True:
        new_token = str(uuid.uuid4())
        with lock:
            previous_qr_token = current_qr_token
            current_qr_token = new_token
        time.sleep(QR_REFRESH_SECONDS)

threading.Thread(target=rotate_token, daemon=True).start()
time.sleep(1)  # give first token a moment

# ─────────────── MODELS ───────────────
class CheckinData(BaseModel):
    ime: str
    jmbag: str
    device_id: str
    token: str

# ─────────────── STUDENT FORM ───────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/form", response_class=HTMLResponse)
async def show_form(request: Request, token: str = ""):
    """
    Renders the student check-in form.
    Token is passed via query param and to template.
    """
    with lock:
        if not current_qr_token:
            return HTMLResponse("<h2>QR još nije spreman. Pokušajte ponovno za nekoliko sekundi.</h2>")

    # Dynamic base URL (works on Render too)
    base_url = str(request.base_url).rstrip('/')

    return templates.TemplateResponse("checkin.html", {
        "request": request,
        "token": token,
        "base_url": base_url
    })

# ─────────────── QR CODE ENDPOINT ───────────────
@app.get("/qr")
async def get_qr(request: Request):
    with lock:
        token = current_qr_token
        if not token:
            raise HTTPException(500, "QR token nije dostupan")

    base_url = str(request.base_url).rstrip('/')
    form_url = f"{base_url}/form?token={quote(token)}"

    img = qrcode.make(form_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# ─────────────── CHECK-IN ENDPOINT ───────────────
@app.post("/checkin")
async def checkin(data: CheckinData):
    with lock:
        # Device already used
        if data.device_id in used_devices:
            raise HTTPException(400, "Ovaj uređaj je već prijavljen u ovoj sesiji.")

        # Token must be current or previous (grace period)
        if data.token not in {current_qr_token, previous_qr_token}:
            raise HTTPException(400, "QR kod je istekao ili nevažeći.")

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
        time.sleep(2)  # check every 2 seconds
        with lock:
            batch = pending_checkins.copy()
            pending_checkins.clear()

        if not batch:
            continue

        try:
            sheet = get_sheet()
            # Only name + jmbag
            rows = [[item["ime"], item["jmbag"]] for item in batch]
            sheet.append_rows(rows)
            print(f"Batch success: {len(rows)} rows written")
        except Exception as e:
            print(f"Batch write failed: {str(e)}")
            # Requeue on failure
            with lock:
                pending_checkins.extend(batch)

threading.Thread(target=batch_writer, daemon=True).start()