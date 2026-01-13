# QR Attendance System

A simple QR-based attendance system built with FastAPI and Google Sheets.

## Features
- Rotating QR codes (anti-sharing)
- One check-in per device
- Google Sheets as backend
- Admin dashboard
- Mobile-friendly

## Setup (Local)

1. Clone repo
2. Create `.env` from `.env.example`
3. Add your Google service account JSON
4. Run:

```bash
uvicorn main:app --reload
