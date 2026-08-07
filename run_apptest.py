"""앱 전체 로직을 시뮬레이션 실행해 예외 없이 렌더되는지 검증 (브라우저 불필요)."""
import os
os.environ["DASH_NO_AUTH"] = "1"   # 검증 시 로그인 게이트 우회(대시보드 본문 렌더)
import sys
sys.stdout.reconfigure(encoding="utf-8")
import time
import truststore
truststore.inject_into_ssl()

import tomllib
from streamlit.testing.v1 import AppTest

with open(".streamlit/secrets.toml", "rb") as f:
    sec = tomllib.load(f)

t0 = time.time()
at = AppTest.from_file("app.py", default_timeout=600)
at.secrets["databricks"] = sec["databricks"]
at.run()
print(f"RUN TIME (1st, includes load): {time.time()-t0:.1f}s")

print("EXCEPTION:", at.exception)
for e in at.error:
    print("ST.ERROR:", e.value)
print("METRICS:", [(m.label, m.value) for m in at.metric])
print("SUBHEADERS:", [s.value for s in at.subheader])
print("OK" if not at.exception else "FAILED")
