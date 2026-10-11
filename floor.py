"""킥스 홍대·성수 층별 매출 — 원천에 층 데이터가 없어 (월×매장×브랜드)→층 매핑을 사용자가 지정.
   매핑은 GCS(floor_map.json)에 저장(store.read_json/write_json). 리프레시가 안 건드리는 별도 파일.
   층 귀속: 각 매출의 월(YYYY-MM)에 해당하는 매장×브랜드 층을 적용 → 월별로 층 구성이 바뀌어도 정확.
"""
from __future__ import annotations

import store

# 층별 매출 대상 매장 (킥스 홍대·성수만). dim_store store_name과 정확히 일치.
FLOOR_STORES = ["무신사 킥스 홍대", "무신사 킥스 성수"]
_MAP = "floor_map"   # {ym: {store: {brand: floor}}}


def get_map() -> dict:
    return store.read_json(_MAP)


def save_assignments(month: str, store_name: str, assignments: dict) -> dict:
    """월×매장의 브랜드→층 지정 저장(빈 층은 제외). 전체 맵 반환."""
    m = store.read_json(_MAP)
    if not isinstance(m, dict):
        m = {}
    m.setdefault(month, {})
    m[month][store_name] = {str(b): str(f).strip() for b, f in (assignments or {}).items() if str(f).strip()}
    store.write_json(_MAP, m)
    return m


def brands_with_stock() -> dict:
    """각 대상 매장의 재고 보유 브랜드 목록 (inventory_goods 매장컬럼>0 × goods_master 브랜드). {store: [brand,...]}."""
    out = {s: [] for s in FLOOR_STORES}
    try:
        igc = [str(c) for c in store.get_inventory_goods().columns]
    except Exception:
        return out
    for s in FLOOR_STORES:
        if s not in igc:
            continue
        try:
            df = store.query(
                f'''SELECT DISTINCT g.brand_nm b FROM inventory_goods iv
                    JOIN goods_master g ON CAST(g.goods_no AS BIGINT)=CAST(iv.goods_no AS BIGINT)
                    WHERE "{s}" > 0 AND g.brand_nm IS NOT NULL AND g.brand_nm <> '' ORDER BY 1''')
            out[s] = [r.b for r in df.itertuples()]
        except Exception:
            pass
    return out


def tab_payload(month: str | None = None) -> dict:
    """층 지정 탭용: 전체 맵 + 대상 매장 + 매장별 재고보유 브랜드 + (옵션)선택월 지정현황."""
    m = get_map()
    return {"stores": FLOOR_STORES, "brands": brands_with_stock(), "map": m,
            "month": month, "assignments": (m.get(month, {}) if month else {})}


def floor_sales(where: str, params: list) -> dict:
    """선택 기간/필터의 킥스 홍대·성수 매출을 (월×매장×브랜드) 집계 → 월별 층맵으로 층 귀속.
       where/params: build_where(f) 결과(매장 필터 제외 권장, 날짜/사업구분/브랜드/카테는 반영). 반환 {store:[{floor,gmv,qty,share}]}."""
    m = get_map()
    ins = ",".join(["?"] * len(FLOOR_STORES))
    w = (where + " AND ") if where else " WHERE "
    try:
        df = store.query(
            f"""SELECT strftime(CAST(sales_date AS DATE), '%Y-%m') ym, store_name, brand_nm,
                       CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(qty) AS DOUBLE) qty
                FROM sales{w}store_name IN ({ins}) GROUP BY 1, 2, 3""", params + list(FLOOR_STORES))
    except Exception:
        return {s: [] for s in FLOOR_STORES}
    agg: dict = {s: {} for s in FLOOR_STORES}
    for r in df.itertuples():
        floor = (m.get(r.ym, {}).get(r.store_name, {}) or {}).get(r.brand_nm) or "미지정"
        a = agg.setdefault(r.store_name, {})
        cell = a.setdefault(floor, {"floor": floor, "gmv": 0.0, "qty": 0.0})
        cell["gmv"] += float(r.gmv or 0); cell["qty"] += float(r.qty or 0)
    out = {}
    for s in FLOOR_STORES:
        rows = sorted(agg.get(s, {}).values(), key=lambda x: -x["gmv"])
        tot = sum(x["gmv"] for x in rows) or 0.0
        for x in rows:
            x["share"] = (x["gmv"] / tot * 100) if tot else 0.0
        out[s] = rows
    return out
