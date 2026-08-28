"""재고 탭 계산 — inventory_pivot(상품·옵션 barcode 단위) 집계.

- 매장별 점재고 컬럼은 한글 매장명이라 DuckDB SQL 대신 pandas로 처리(컬럼명=store_name과 동일).
- 필터: 사업구분(biz) + 매장타입(type, 보이는 매장 컬럼 결정). 기간 필터 미적용(스냅샷).
- 창고(허브): MFS / 허브1000 / 허브1700, 합계=허브합계. 점재고합계=선택 매장 컬럼 합.
"""
from __future__ import annotations

import math

import pandas as pd

import store

_META_ASCII = {"barcode", "goods_no", "goods_opt", "brand_nm", "goods_nm", "cat_top", "cat_large",
               "cat_medium", "off_md_id", "company_id", "brand_id", "business_type", "concept"}


def _f(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(x) or math.isinf(x) else x


def _cols(columns):
    cols = [str(c) for c in columns]
    hub_total = next((c for c in cols if c.endswith("합계") and "허브" in c), None)
    jaego_total = next((c for c in cols if c.endswith("합계") and "점재고" in c), None)
    mfs = next((c for c in cols if c == "MFS"), None)
    h1000 = next((c for c in cols if "1000" in c), None)
    h1700 = next((c for c in cols if "1700" in c), None)
    hubcols = [c for c in [mfs, h1000, h1700] if c]
    skip = set(_META_ASCII) | {hub_total, jaego_total} | set(hubcols)
    store_cols = [c for c in cols if c not in skip]
    return store_cols, hubcols, jaego_total, hub_total


def _store_types() -> dict:
    df = store.query("SELECT DISTINCT store_name, shop_type FROM sales")
    return {r.store_name: r.shop_type for r in df.itertuples()}


def _prep(f):
    """f = 공통 필터 dict(biz/type/store/brand/cat_*/md/goods). 점재고 = 보이는 매장(store/type) 합."""
    f = f or {}
    inv = store.get_inventory_pivot()
    store_cols, hubcols, jcol, hcol = _cols(inv.columns)
    stype = _store_types()
    fstore = f.get("store") or []
    ftype = f.get("type") or []
    if fstore:                                   # 매장 직접 선택 우선
        vis = [s for s in store_cols if s in fstore]
    elif ftype:
        vis = [s for s in store_cols if stype.get(s) in ftype]
    else:
        vis = store_cols

    df = inv
    if f.get("biz"):
        df = df[df["business_type"].isin(f["biz"])]
    if f.get("brand"):
        df = df[df["brand_nm"].isin(f["brand"])]
    if f.get("brand_ex"):                        # 브랜드 제외
        df = df[~df["brand_nm"].isin(f["brand_ex"])]
    for col in ("cat_top", "cat_large", "cat_medium"):
        if f.get(col) and col in df.columns:
            df = df[df[col].isin(f[col])]
    if f.get("md") and "off_md_id" in df.columns:
        df = df[df["off_md_id"].isin(f["md"])]
    if f.get("goods"):
        gset = {str(x) for x in f["goods"]}
        df = df[df["goods_no"].astype(str).isin(gset)]
    if f.get("name_like"):                       # 상품명 부분일치(대소문자 무시) — 예: ACG
        nl = str(f["name_like"]).strip().lower()
        if nl:
            df = df[df["goods_nm"].astype(str).str.lower().str.contains(nl, na=False, regex=False)]
    df = df[df["goods_nm"].astype(str).str.strip() != ""].copy()
    df["__jaego"] = df[vis].sum(axis=1) if vis else 0
    df["__hub"] = df[hcol].fillna(0) if hcol else 0
    df = df[(df["__jaego"] > 0) | (df["__hub"] > 0)]
    return df, vis, hubcols, hcol


def _cat_pies(df, top_n=8):
    """카테고리별(최상위/대/중) **점재고(선택 매장)** 구성 — 원형그래프용. 상위 top_n + '기타'.
       (허브/창고 재고는 제외 — 매장 진열/판매 관점 점재고만.)"""
    total = df["__jaego"].fillna(0)
    out = {}
    for key in ("cat_top", "cat_large", "cat_medium"):
        if key not in df.columns:
            out[key] = []
            continue
        cat = df[key].astype(str).str.strip().replace("", "(미분류)").fillna("(미분류)")
        g = total.groupby(cat).sum()
        g = g[g > 0].sort_values(ascending=False)
        items = [{"name": str(k), "value": _f(v)} for k, v in g.items()]
        if len(items) > top_n:
            head = items[:top_n]
            etc = sum(x["value"] for x in items[top_n:])
            head.append({"name": f"기타 {len(items) - top_n}종", "value": _f(etc)})
            items = head
        out[key] = items
    return out


def _brand_stock(df, vis, top_n=20):
    """브랜드별 점재고(선택 매장) — 사업구분(위탁/매입/기타) 스택 + 전체 대비 비중. 상위 top_n.
       매장별 재고수량 차트와 동일 스키마({name,위탁,매입,기타,total}) + share."""
    if not vis or df.empty:
        return []
    grand = float(df["__jaego"].sum()) or 0.0
    piv = df.groupby(["brand_nm", "business_type"])["__jaego"].sum().unstack(fill_value=0.0)
    rows = []
    for brand, row in piv.iterrows():
        wt = float(row.get("위탁", 0.0)); mi = float(row.get("매입", 0.0))
        etc = float(row.sum()) - wt - mi          # 기타 + 그 외 사업구분
        total = wt + mi + etc
        if total <= 0:
            continue
        rows.append({"name": (str(brand) or "(미매칭)"), "위탁": _f(wt), "매입": _f(mi), "기타": _f(etc),
                     "total": _f(total), "share": round(total / grand * 100, 1) if grand else 0.0})
    rows.sort(key=lambda x: -x["total"])
    return rows[:top_n]


def compute(f=None, limit=300):
    df, vis, hubcols, hcol = _prep(f)
    if df.empty:
        return {"empty": True, "kpis": {"jaego": 0, "hub": 0, "options": 0, "goods": 0},
                "stores": [], "rows": [], "store_cols": [], "hubcols": hubcols, "cats": {}, "brand_stock": []}

    kpis = {"jaego": _f(df["__jaego"].sum()), "hub": _f(df["__hub"].sum()),
            "options": int(len(df)), "goods": int(df["goods_no"].nunique())}
    cats = _cat_pies(df)

    # 매장별 재고 (위탁/매입) — store × business_type
    stores = []
    if vis:
        g = df.groupby("business_type")[vis].sum()  # index=business_type, cols=stores
        for s in vis:
            row = {"name": s, "위탁": 0.0, "매입": 0.0, "기타": 0.0, "total": 0.0}
            for bt in g.index:
                row[str(bt)] = _f(g.loc[bt, s])
            row["total"] = _f(sum(row[k] for k in ("위탁", "매입", "기타")))
            if row["total"] > 0:
                stores.append(row)
        stores.sort(key=lambda r: r["total"])

    # 상품·옵션 표 (재고순 상위 limit) — ⚠️ to_dict로 실제 컬럼명(한글 매장명 포함) 보존
    # (itertuples는 밑줄/한글/공백 컬럼명을 _0..으로 renames → 점재고/허브/매장값이 0으로 깨짐)
    idcols = [c for c in ("brand_nm", "goods_nm", "goods_no", "goods_opt", "business_type",
                          "cat_top", "cat_large", "cat_medium") if c in df.columns]
    numcols = vis + hubcols + ["__jaego", "__hub"]
    # 점재고합계 → 허브합계 순 내림차순
    disp = df.sort_values(["__jaego", "__hub"], ascending=[False, False]).head(limit)
    sub = disp[idcols + numcols].copy()
    sub[numcols] = sub[numcols].fillna(0).round().astype("int64")
    for c in idcols:
        if sub[c].dtype == object:
            sub[c] = sub[c].fillna("")
    sub["goods_no"] = sub["goods_no"].astype("int64")
    sub = sub.rename(columns={"__jaego": "점재고합계", "__hub": "허브합계"})
    rows = sub.to_dict(orient="records")
    return {"empty": False, "kpis": kpis, "stores": stores, "rows": rows,
            "store_cols": vis, "hubcols": hubcols, "cats": cats,
            "brand_stock": _brand_stock(df, vis)}


def csv_rows(f=None):
    """재고 CSV — **tidy/long**: (상품옵션 × 위치)당 1행. 화면 피벗(매장=열)을 풀어 매장/창고별 행으로.
       열 = 상품속성 + 위치구분(매장/허브) + 위치(매장·창고명) + 재고수량. 재고 0인 위치는 제외."""
    df, vis, hubcols, hcol = _prep(f)
    base = [c for c in ("brand_nm", "goods_nm", "goods_no", "goods_opt", "business_type",
                        "cat_top", "cat_large", "cat_medium") if c in df.columns]
    header = base + ["위치구분", "위치", "재고수량"]
    if df.empty:
        return header, []
    loccols = vis + hubcols                         # 매장 컬럼 + 허브(MFS/1000/1700)
    df = df.sort_values(["__jaego", "__hub"], ascending=[False, False])
    sub = df[base + loccols].copy()
    sub[loccols] = sub[loccols].fillna(0).round().astype("int64")
    if "goods_no" in sub.columns:   # 미매핑 바코드는 goods_no=NaN → astype 예외 방지(빈칸 처리)
        sub["goods_no"] = sub["goods_no"].map(lambda x: "" if pd.isna(x) else int(x))
    for c in base:
        if sub[c].dtype == object:
            sub[c] = sub[c].fillna("")
    storeset = set(vis)
    out = []
    for d in sub.to_dict(orient="records"):
        basevals = [d[c] for c in base]
        for loc in loccols:                         # 위치(매장/창고) 컬럼을 행으로 melt
            qty = d[loc]
            if qty:                                 # 재고 있는 위치만 (0/NaN 제외)
                out.append(basevals + ["매장" if loc in storeset else "허브", loc, qty])
    return header, out
