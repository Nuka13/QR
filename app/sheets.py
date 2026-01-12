import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os

# Updated scopes – this is the most common fix for this exact error
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Path to your service account JSON (looks correct)
CREDS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH")


# Name of the Google Sheet (must match exactly – case sensitive!)
SHEET_NAME = "Attendance"

def get_sheet():
    """
    Authenticate with Google and return the first worksheet.
    """
    creds = Credentials.from_service_account_file(
        CREDS_PATH,
        scopes=SCOPES
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open(SHEET_NAME)
    return spreadsheet.sheet1

def append_checkin(name: str, student_number: str, class_id: str, device_id: str):
    """
    Append a single attendance row to the Google Sheet.
    """
    sheet = get_sheet()
    timestamp = datetime.utcnow().isoformat()   # or .strftime("%Y-%m-%d %H:%M:%S") if you prefer

    row = [
        name,
        student_number,
        class_id,
        device_id,
        timestamp
    ]

    sheet.append_row(row)