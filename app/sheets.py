import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import json

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

SHEET_NAME = "Attendance"

def get_sheet():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON environment variable is missing")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).sheet1

def append_checkin(name: str, student_number: str, device_id: str):
    sheet = get_sheet()
    sheet.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        name,
        student_number,
        device_id
    ])
```

Changes: added a timestamp column (most important for attendance), removed the unused `class_id` parameter since your `CheckinData` model doesn't have it, and cleaned up `append_checkin` to match what `main.py` actually sends.

---

As for `GOOGLE_CREDENTIALS_JSON` — I can't generate that for you because it's a real secret tied to **your** Google Cloud service account. To get it:

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Your project → **IAM & Admin → Service Accounts**
3. Click your service account → **Keys → Add Key → Create new key → JSON**
4. Open the downloaded file, copy the entire contents
5. Paste it as the value of `GOOGLE_CREDENTIALS_JSON` on Render — it'll look like:
```
GOOGLE_CREDENTIALS_JSON={"type":"service_account","project_id":"qr-attendance-484118","private_key_id":"6cfb69...","private_key":"-----BEGIN RSA PRIVATE KEY-----\n...","client_email":"...@qr-attendance-484118.iam.gserviceaccount.com",...}
