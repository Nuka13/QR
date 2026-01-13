from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from app.sheets import append_checkin
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

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
QR_REFRESH_SECONDS = 20

# ────────────────────────────────────────────────
# Global state
# ────────────────────────────────────────────────

current_qr_token: Optional[str] = None
used_devices = set()
lock = threading.Lock()

# ────────────────────────────────────────────────
# Background QR rotator (FIXED)
# ────────────────────────────────────────────────

def rotate_token():
    global current_qr_token
    while True:
        with lock:
            current_qr_token = str(uuid.uuid4())
        time.sleep(QR_REFRESH_SECONDS)

@app.on_event("startup")
def start_qr_rotator():
    threading.Thread(target=rotate_token, daemon=True).start()

# ────────────────────────────────────────────────
# Models
# ────────────────────────────────────────────────

class CheckinData(BaseModel):
    ime: str
    jmbag: str
    device_id: str
    token: str

# ────────────────────────────────────────────────
# Student form
# ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
@app.get("/form", response_class=HTMLResponse)
async def show_form(request: Request, token: str | None = None):
    with lock:
        if not token or token != current_qr_token:
            return HTMLResponse("<h2>QR kod je istekao. Skeniraj novi.</h2>")

    return templates.TemplateResponse(
        "checkin.html",
        {"request": request, "token": token}
    )

# ────────────────────────────────────────────────
# QR endpoint (FIXED)
# ────────────────────────────────────────────────

@app.get("/qr")
async def get_qr(request: Request):
    with lock:
        token = current_qr_token

    if not token:
        raise HTTPException(500, "QR token nije spreman")

    base_url = str(request.base_url).rstrip("/")
    form_url = f"{base_url}/form?token={quote(token)}"

    img = qrcode.make(form_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/png")

# ────────────────────────────────────────────────
# Check-in
# ────────────────────────────────────────────────

@app.post("/checkin")
async def checkin(data: CheckinData):
    with lock:
        if data.token != current_qr_token:
            raise HTTPException(400, "QR kod je istekao")

        if data.device_id in used_devices:
            raise HTTPException(400, "Uređaj već prijavljen")

        used_devices.add(data.device_id)

    append_checkin(
        name=data.ime,
        student_number=data.jmbag,
        class_id="",
        device_id=data.device_id
    )

    return {"status": "success"}

# ────────────────────────────────────────────────
# Admin
# ────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, password: str = None):
    if password != ADMIN_PASSWORD:
        return HTMLResponse("""
        <form method="get">
            <h2>Admin</h2>
            <input type="password" name="password">
            <button>Login</button>
        </form>
        """)

    with lock:
        device_count = len(used_devices)

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "device_count": device_count,
            "password": ADMIN_PASSWORD
        }
    )

@app.post("/admin/reset")
async def reset_session(password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        raise HTTPException(403)

    with lock:
        used_devices.clear()

    return {"status": "Reset OK"}
