@echo off
REM React dashboard dev server (Vite). LAN accessible on port 5173.
REM Needs the API running too (run_api.cmd). Open http://localhost:5173
set NODE_OPTIONS=--use-system-ca
set PATH=C:\Users\MUSINSA\node\node-v24.18.0-win-x64;%PATH%
cd /d "%~dp0web"
npm run dev
