"""접속 + 핵심 쿼리 검증 (Streamlit 없이 단독 실행)."""
try:
    import truststore
    truststore.inject_into_ssl()
    print("[ok] truststore injected (Windows cert store)")
except Exception as e:
    print("[warn] truststore:", e)

import tomllib
from databricks import sql

with open(".streamlit/secrets.toml", "rb") as f:
    cfg = tomllib.load(f)["databricks"]

conn = sql.connect(
    server_hostname=cfg["host"], http_path=cfg["http_path"], access_token=cfg["token"]
)
print("[ok] connected")

with conn.cursor() as cur:
    cur.execute("SELECT 1")
    print("[ok] SELECT 1 ->", cur.fetchone()[0])

# fact 로직 스모크 테스트 (최근 7일 KPI)
import db  # noqa: E402  (truststore already injected)

# db.run_df uses streamlit secrets/cache; here just reuse the connection directly
sql_kpi = db.BASE_FACT + (
    "SELECT CAST(SUM(gmv_amt) AS DOUBLE) gmv, CAST(SUM(net_qty) AS DOUBLE) qty, COUNT(*) cnt "
    "FROM fact WHERE sales_date >= DATE_SUB(CURRENT_DATE(), 7)"
)
with conn.cursor() as cur:
    cur.execute(sql_kpi)
    row = cur.fetchone()
    print(f"[ok] last7d  GMV={row[0]:,.0f}  qty={row[1]:,.0f}  lines={row[2]:,}")

conn.close()
print("[done]")
