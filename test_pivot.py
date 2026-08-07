import sys
sys.stdout.reconfigure(encoding="utf-8")
import truststore
truststore.inject_into_ssl()
import time
import db

t = time.time()
df = db.load_inventory_pivot()
print(f"LOADED {len(df):,} rows in {time.time()-t:.1f}s")
print("HUBSUMS:", {k: float(df[k].sum()) for k in ["MFS", "허브1000", "허브1700", "점재고합계"]})
hav = df[df["brand_nm"].str.contains("하바이", na=False)]
print(f"하바이아나스 rows: {len(hav)}  허브1700합: {float(hav['허브1700'].sum()):,.0f}  점재고합: {float(hav['점재고합계'].sum()):,.0f}")
print(hav[["brand_nm", "goods_nm", "goods_opt", "점재고합계", "허브1000", "허브1700", "MFS"]].head(6).to_string())
print("DONE")
