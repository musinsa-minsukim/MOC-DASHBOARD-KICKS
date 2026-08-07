@echo off
REM FastAPI backend (dashboard API). LAN accessible on port 8000.  Docs: http://localhost:8000/docs
cd /d "%~dp0"
set UV_SYSTEM_CERTS=1
"C:\Users\MUSINSA\.local\bin\uv.exe" run uvicorn api:app --host 0.0.0.0 --port 8000
