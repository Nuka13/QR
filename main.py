from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from app.sheets import get_sheet
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

# ─────────────── CONFIG ───────────────
# SESSION_SECRET can be any long random string
SESSION_SECRET = os.getenv("SESSION_SECRET", "mom-knows-best-security-key-123")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
QR_REFRESH_SECONDS = 20
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# Add the Session Middleware
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

templates = Jinja2Templates(directory="templates")

# ─────────────── GLOBAL STATE ───────────────
current_qr_token: Optional[str] = None
previous_qr_token: Optional[str] = None
used_devices = set()
pending_checkins = []
lock = threading.Lock()

def rotate_token():
    global current_qr_token, previous_qr_token
    while True:
        new_token = str(uuid.uuid4())
        with lock:
            previous_qr_token = current_qr_token
            current_qr_token = new_token
        time.sleep(QR_REFRESH_SECONDS)

threading.Thread(target=rotate_token, daemon=True).start()

class CheckinData(BaseModel):
    ime: str
    jmbag: str
    device_id: str
    token: str

# ─────────────── AUTH ROUTES ───────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return """
    <body style="font-family: Arial; display: flex; justify-content: center; padding-top: 100px; background: #f4f4f9;">
        <form method="post" style="background: white; padding: 30px; border-radius: 8px; shadow: 0 2px 10px rgba(0,0,0,0.1);">
            <h2>Admin Prijava</h2>
            <input type="password" name="password" placeholder="Lozinka" autofocus style="padding: 10px; width: 200px;"><br><br>
            <button type="submit" style="width: 100%; padding: 10px; background: #4CAF50; color: white; border: none; cursor: pointer;">Ulaz</button>
        </form>
    </body>
    """

@app.post("/login")
async def login_action(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["is_admin"] = True
        return RedirectResponse(url="/admin", status_code=303)
    return HTMLResponse("Pogrešna lozinka. <a href='/login'>Pokušaj ponovno</a>")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")

# ─────────────── ADMIN & APP ROUTES ───────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/login")
    
    with lock:
        device_count = len(used_devices)

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "device_count": device_count,
        "qr_refresh": QR_REFRESH_SECONDS
    })

@app.get("/qr")
async def get_qr(request: Request):
    with lock:
        token = current_qr_token
    if not token:
        raise HTTPException(500, "Token nije generiran")
        
    base_url = str(request.base_url).rstrip('/')
    form_url = f"{base_url}/form?token={quote(token)}"
    
    img = qrcode.make(form_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.post("/checkin")
async def checkin(data: CheckinData):
    with lock:
        if data.device_id in used_devices:
            raise HTTPException(400, "Uređaj već prijavljen.")
        if data.token not in {current_qr_token, previous_qr_token}:
            raise HTTPException(400, "QR kod istekao.")
        
        used_devices.add(data.device_id)
        pending_checkins.append(data.dict())
    return {"status": "success"}

@app.get("/form", response_class=HTMLResponse)
async def show_form(request: Request, token: str = ""):
    return templates.TemplateResponse("checkin.html", {"request": request, "token": token})

@app.post("/admin/reset")
async def reset_session(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(403)
    with lock:
        used_devices.clear()
        pending_checkins.clear()
    return {"status": "Sesija resetirana"}

# ─────────────── BATCH WRITER ───────────────
def batch_writer():
    while True:
        time.sleep(2)
        with lock:
            batch = pending_checkins.copy()
            pending_checkins.clear()
        if not batch: continue
        try:
            sheet = get_sheet()
            rows = [[c["ime"], c["jmbag"], c["device_id"]] for c in batch]
            sheet.append_rows(rows)
        except Exception as e:
            with lock: pending_checkins.extend(batch)
            print("Greška pri pisanju u Sheets:", e)

threading.Thread(target=batch_writer, daemon=True).start()
