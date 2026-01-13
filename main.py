from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from app.sheets import append_checkin
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

# ────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
QR_REFRESH_SECONDS = 20
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# ────────────────────────────────────────────────
# Global state
# ────────────────────────────────────────────────
current_qr_token: Optional[str] = None
last_qr_token: Optional[str] = None
used_devices = set()  # device_ids already checked in this session
lock = threading.Lock()

# ────────────────────────────────────────────────
# Background QR rotator
# ────────────────────────────────────────────────
def rotate_token():
    global current_qr_token, last_qr_token
    while True:
        new_token = str(uuid.uuid4())
        with lock:
            last_qr_token = current_qr_token  # store previous token
            current_qr_token = new_token
        time.sleep(QR_REFRESH_SECONDS)

threading.Thread(target=rotate_token, daemon=True).start()
time.sleep(1)  # allow first token to generate

# ────────────────────────────────────────────────
# Models
# ────────────────────────────────────────────────
class CheckinData(BaseModel):
    ime: str
    jmbag: str
    device_id: str
    token: str  # token sent from QR

# ────────────────────────────────────────────────
# Student form
# ────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/form", response_class=HTMLResponse)
async def show_form(request: Request, token: Optional[str] = None):
    with lock:
        if not current_qr_token:
            return HTMLResponse("<h2>QR još nije spreman. Pokušajte ponovno za nekoliko sekundi.</h2>")

    return templates.TemplateResponse("checkin.html", {
        "request": request,
        "token": token or current_qr_token  # send current token to form
    })

# ────────────────────────────────────────────────
# QR code endpoint
# ────────────────────────────────────────────────
@app.get("/qr")
async def get_qr(request: Request):
    with lock:
        if not current_qr_token:
            raise HTTPException(500, "QR token nije dostupan")
        token_to_use = current_qr_token

    base_url = str(request.base_url).rstrip('/')
    form_url = f"{base_url}/form?token={quote(token_to_use)}"

    img = qrcode.make(form_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# ────────────────────────────────────────────────
# Check-in endpoint
# ────────────────────────────────────────────────
@app.post("/checkin")
async def checkin(data: CheckinData):
    with lock:
        # Reject already used devices
        if data.device_id in used_devices:
            raise HTTPException(400, "Ovaj uređaj je već prijavljen u ovoj sesiji.")

        # Validate QR token (current or last)
        valid_tokens = [t for t in [current_qr_token, last_qr_token] if t]
        if data.token not in valid_tokens:
            raise HTTPException(400, "QR kod je istekao.")

        # Mark device as used
        used_devices.add(data.device_id)

    try:
        append_checkin(
            name=data.ime,
            student_number=data.jmbag,
            class_id="",
            device_id=data.device_id
        )
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(500, f"Greška pri spremanju: {str(e)}")

# ────────────────────────────────────────────────
# Admin dashboard
# ────────────────────────────────────────────────
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
    return {"status": "Sesija resetirana"}

