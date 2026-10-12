"""킥스 홍대·성수 층별 매출 — 원천에 층 데이터가 없어 (월×매장×브랜드)→층 매핑을 사용자가 지정.
   매핑은 GCS(floor_map.json)에 저장(store.read_json/write_json). 리프레시가 안 건드리는 별도 파일.
   층 귀속: 각 매출의 월(YYYY-MM)에 해당하는 매장×브랜드 층을 적용 → 월별로 층 구성이 바뀌어도 정확.
"""
from __future__ import annotations

import store

# 층별 매출 대상 매장 (킥스 홍대·성수만). dim_store store_name과 정확히 일치.
FLOOR_STORES = ["무신사 킥스 홍대", "무신사 킥스 성수"]
_MAP = "floor_map"   # {ym: {store: {brand: floor}}}

# 기준 월: 사용자가 처음 세팅한 월. 명시 설정이 없는 월의 기본값 해석 기준.
#   · 과거 월(< BASE): 기본값 = BASE 월 설정
#   · 미래 월(> BASE): 기본값 = 직전(이하) 명시월 설정을 이월(없으면 BASE)
#   · 각 월은 사용자가 그 달을 직접 저장하면 그 달 명시값이 우선
BASE_MONTH = "2026-10"


def get_map() -> dict:
    return store.read_json(_MAP)


def _store_explicit(m: dict, store_name: str, mm: str) -> dict | None:
    """m에 (mm×store) 명시 설정이 있고 비어있지 않으면 그 dict, 아니면 None."""
    d = (m.get(mm, {}) or {}).get(store_name, {}) or {}
    return d if d else None


def effective_for_store(m: dict, store_name: str, month: str) -> tuple[dict, str | None]:
    """month×store에 적용할 (브랜드→층 dict, 값의 출처월). 출처월이 month와 같으면 명시 설정.
       명시 없으면: 미래월=직전 명시월 이월(없으면 BASE), 과거월=BASE."""
    if not month:
        return {}, None
    d = _store_explicit(m, store_name, month)
    if d is not None:
        return d, month                       # 해당 월 직접 설정
    if month >= BASE_MONTH:
        cands = sorted(mm for mm in m.keys() if mm <= month and _store_explicit(m, store_name, mm) is not None)
        src = cands[-1] if cands else BASE_MONTH   # 직전(이하) 명시월 이월
    else:
        src = BASE_MONTH                       # 과거월 → 기준월
    ed = _store_explicit(m, store_name, src)
    return (ed or {}), (src if ed else None)


def effective_map(month: str) -> dict:
    """선택 월의 전 매장 유효 층맵 {store: {brand: floor}} (상속 반영)."""
    m = get_map()
    return {s: effective_for_store(m, s, month)[0] for s in FLOOR_STORES}


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
    """층 지정 탭용: 전체 맵 + 대상 매장 + 매장별 재고보유 브랜드 + 선택월 유효값(상속 반영).
       effective[store]=프리필할 브랜드→층, source[store]=값 출처월(=month면 명시저장, ≠면 상속), base=기준월."""
    m = get_map()
    eff: dict = {}
    src: dict = {}
    if month:
        for s in FLOOR_STORES:
            d, sm = effective_for_store(m, s, month)
            eff[s] = d
            src[s] = sm
    return {"stores": FLOOR_STORES, "brands": brands_with_stock(), "map": m,
            "month": month, "base": BASE_MONTH, "effective": eff, "source": src,
            "assignments": (m.get(month, {}) if month else {})}


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
    eff_cache: dict = {}   # (ym, store) → 유효 층맵 (상속 반영, 월별 1회 계산)
    for r in df.itertuples():
        key = (r.ym, r.store_name)
        fmap = eff_cache.get(key)
        if fmap is None:
            fmap = effective_for_store(m, r.store_name, r.ym)[0]
            eff_cache[key] = fmap
        floor = fmap.get(r.brand_nm) or "미지정"
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
