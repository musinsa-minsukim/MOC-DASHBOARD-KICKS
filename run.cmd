@echo off
REM 무신사 오프라인 대시보드 - 로컬 dev 서버
REM  - LAN 접속 가능(0.0.0.0): 같은 와이파이의 폰에서 http://<PC-IP>:8501
REM  - 코드 저장 시 자동 리런(runOnSave)
REM  - 이 창을 닫으면 서버 종료. 상시 띄워두면 dev 서버처럼 사용.
cd /d "%~dp0"
set UV_SYSTEM_CERTS=1
"C:\Users\MUSINSA\.local\bin\uv.exe" run streamlit run app.py ^
  --server.port 8501 ^
  --server.address 0.0.0.0 ^
  --server.headless true ^
  --server.runOnSave true ^
  --browser.gatherUsageStats false
