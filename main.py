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
import os 

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ────────────────────────────────────────────────
# Configuration – change these as needed
# ────────────────────────────────────────────────

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")          # ← hardcoded for now – change this!
SESSION_ID = "Razred-3c"              # ← can be changed per class
QR_REFRESH_SECONDS = 20               # how often QR changes
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")   # ← CHANGE THIS when deploying (or use request.url)

# ────────────────────────────────────────────────
# Global state
# ────────────────────────────────────────────────

current_qr_token: Optional[str] = None
used_devices = set()                  # device_ids already checked in this session

lock = threading.Lock()

# ────────────────────────────────────────────────
# Background QR rotator
# ────────────────────────────────────────────────

def rotate_token():
    global current_qr_token
    while True:
        new_token = str(uuid.uuid4())
        with lock:
            current_qr_token = new_token
        time.sleep(QR_REFRESH_SECONDS)

threading.Thread(target=rotate_token, daemon=True).start()

# Give first token time to appear
time.sleep(1)

# ────────────────────────────────────────────────
# Models
# ────────────────────────────────────────────────

class CheckinData(BaseModel):
    ime: str
    jmbag: str
    device_id: str

# ────────────────────────────────────────────────
# Student form (served by FastAPI)
# ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
@app.get("/form", response_class=HTMLResponse)
async def show_form(request: Request):
    with lock:
        token = current_qr_token
        if not token:
            return HTMLResponse("<h2>QR još nije spreman. Pokušajte ponovno za nekoliko sekundi.</h2>")

    form_url = f"{BASE_URL}/form"  # self-reference

    return templates.TemplateResponse("checkin.html", {
        "request": request,
        "token": token,
        "session_id": SESSION_ID
    })

# ────────────────────────────────────────────────
# QR code endpoint (teacher projects this)
# ────────────────────────────────────────────────

@app.get("/qr")
async def get_qr():
    with lock:
        token = current_qr_token
        if not token:
            raise HTTPException(500, "QR token nije dostupan")

    # QR points to the form with current token in query param
    form_url = f"{BASE_URL}/form?token={quote(token)}"

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
        if data.device_id in used_devices:
            raise HTTPException(400, "Ovaj uređaj je već prijavljen u ovoj sesiji.")

        used_devices.add(data.device_id)

    try:
        append_checkin(
            name=data.ime,
            student_number=data.jmbag,
            class_id=SESSION_ID,
            device_id=data.device_id
        )
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(500, f"Greška pri spremanju: {str(e)}")

# ────────────────────────────────────────────────
# Admin dashboard – simple password protected page
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
        "session_id": SESSION_ID,
        "device_count": device_count,
        "qr_url": "/qr",
        "password": ADMIN_PASSWORD  # for reset link
    })

@app.post("/admin/reset")
async def reset_session(password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        raise HTTPException(403, "Pogrešna lozinka")

    with lock:
        used_devices.clear()

    return {"status": "Sesija resetirana", "new_session_id": SESSION_ID}