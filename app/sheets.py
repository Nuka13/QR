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

    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=SCOPES
    )

    client = gspread.authorize(creds)
    spreadsheet = client.open(SHEET_NAME)
    return spreadsheet.sheet1

def append_checkin(name: str, student_number: str, class_id: str, device_id: str):
    sheet = get_sheet()
    timestamp = datetime.utcnow().isoformat()

    sheet.append_row([
        name,
        student_number,
        class_id,
        device_id,
        timestamp
    ])
