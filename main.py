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
SESSION_SECRET = os.getenv("SESSION_SECRET")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
QR_REFRESH_SECONDS = 20

if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET not set")
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD not set")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=True,
    same_site="lax"
)

templates = Jinja2Templates(directory="templates")

# ─────────────── GLOBAL STATE ───────────────
current_qr_token: Optional[str] = str(uuid.uuid4())
previous_qr_token: Optional[str] = None
token_last_rotated = time.time()

used_devices = set()
pending_checkins = []
lock = threading.Lock()


# ─────────────── TOKEN ROTATION ───────────────
def rotate_token():
    global current_qr_token, previous_qr_token, token_last_rotated
    while True:
        time.sleep(QR_REFRESH_SECONDS)
        with lock:
            previous_qr_token = current_qr_token
            current_qr_token = str(uuid.uuid4())
            token_last_rotated = time.time()

threading.Thread(target=rotate_token, daemon=True).start()


# ─────────────── MODELS ───────────────
class CheckinData(BaseModel):
    ime: str
    jmbag: str
    device_id: str
    token: str


# ─────────────── AUTH ───────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Redirect if already logged in
    if request.session.get("is_admin"):
        return RedirectResponse(url="/admin")
    return """
    <body style="font-family: Arial; display: flex; justify-content: center; padding-top: 100px; background: #f4f4f9;">
        <form method="post" style="background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
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
    # Use 303 redirect back to login on failure to avoid re-POST on refresh
    return HTMLResponse(
        "Pogrešna lozinka. <a href='/login'>Pokušaj ponovno</a>",
        status_code=401
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ─────────────── ADMIN ───────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/login", status_code=303)

    with lock:
        device_count = len(used_devices)
        seconds_left = QR_REFRESH_SECONDS - int(time.time() - token_last_rotated)

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "device_count": device_count,
        "qr_refresh": QR_REFRESH_SECONDS,
        "seconds_left": max(seconds_left, 0)
    })


@app.post("/admin/reset")
async def reset_session(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")

    with lock:
        used_devices.clear()
        pending_checkins.clear()

    return {"status": "resetirano"}


# ─────────────── QR ───────────────
@app.get("/qr")
async def get_qr(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")

    with lock:
        token = current_qr_token

    base_url = str(request.base_url).rstrip('/')
    form_url = f"{base_url}/form?token={quote(token)}"

    img = qrcode.make(form_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="image/png",
        headers={"Cache-Control": "no-store"}  # prevent browser caching old QR
    )


# ─────────────── FORM ───────────────
@app.get("/form", response_class=HTMLResponse)
async def show_form(request: Request, token: str = ""):
    return templates.TemplateResponse("checkin.html", {
        "request": request,
        "token": token
    })


# ─────────────── CHECKIN ───────────────
@app.post("/checkin")
async def checkin(data: CheckinData):
    with lock:
        if data.device_id in used_devices:
            raise HTTPException(status_code=400, detail="Uređaj već prijavljen.")

        if data.token not in {current_qr_token, previous_qr_token}:
            raise HTTPException(status_code=400, detail="QR kod istekao.")

        used_devices.add(data.device_id)
        pending_checkins.append(data.dict())

    return {"status": "success"}


# ─────────────── BATCH WRITER ───────────────
def batch_writer():
    while True:
        time.sleep(2)
        with lock:
            if not pending_checkins:
                continue
            batch = pending_checkins.copy()
            pending_checkins.clear()

        try:
            sheet = get_sheet()
            rows = [[c["ime"], c["jmbag"], c["device_id"]] for c in batch]
            sheet.append_rows(rows)
        except Exception as e:
            print("Greška pri pisanju:", e)
            with lock:                      # re-acquire lock safely for re-insert
                pending_checkins.extend(batch)

threading.Thread(target=batch_writer, daemon=True).start()
