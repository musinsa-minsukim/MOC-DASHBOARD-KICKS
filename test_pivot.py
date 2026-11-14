"""재고 피벗 로더 스모크 — 통합 원장(team.scm.stock) 기준. 실행: .venv/Scripts/python.exe test_pivot.py"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import truststore
truststore.inject_into_ssl()
import time
import db

t = time.time()
df = db.load_inventory_pivot_unified()
print(f"LOADED {len(df):,} rows in {time.time()-t:.1f}s")
cols = [c for c in db.LOGI_COLS if c in df.columns] + ["점재고합계"]
print("SUMS:", {k: float(df[k].sum()) for k in cols})
print("stock_basis:", df.attrs.get("stock_basis"), "| data_date:", df.attrs.get("data_date"))
print("DONE")
