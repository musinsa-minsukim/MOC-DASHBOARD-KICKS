"""재고 탭 계산 — inventory_pivot(상품·옵션 barcode 단위) 집계.

- 매장별 점재고 컬럼은 한글 매장명이라 DuckDB SQL 대신 pandas로 처리(컬럼명=store_name과 동일).
- 필터: 사업구분(biz) + 매장타입(type, 보이는 매장 컬럼 결정). 기간 필터 미적용(스냅샷).
- 창고(허브): MFS / 허브1000 / 허브1700, 합계=허브합계. 점재고합계=선택 매장 컬럼 합.
"""
from __future__ import annotations

import math
import re

import pandas as pd

import store

_META_ASCII = {"barcode", "goods_no", "goods_opt", "brand_nm", "goods_nm", "cat_top", "cat_large",
               "cat_medium", "off_md_id", "company_id", "brand_id", "business_type", "concept",
               "is_running"}

# 옵션 문자열에서 컬러/사이즈 분리 규칙(원천 goods_opt 실측 기반):
#  - '^' 있으면 → 앞=컬러, 뒤=사이즈 (예: '블랙^M', '화이트/그레이^L' — '/'는 컬러명 일부)
#  - '^'없고 '/'있는데 앞 토큰이 '사이즈'가 아니면 → 컬러/사이즈 (예: 'black/free', 'NAVY/OS')
#  - 그 외(M, 250, O/S, 240/M5W7 등 사이즈만) → 컬러는 UID 레벨(1 UID = 1 컬러)
# → 컬러SKU = distinct (goods_no × 컬러), 바코드SKU = distinct barcode(컬러×사이즈)
_SIZE_RE = re.compile(
    r'^([0-9]+|xxs|xs|s|m|l|xl|xxl|[234]xl|f|free|os|one|onesize|o|w[0-9]+|m[0-9].*|[0-9]+호.*)$',
    re.IGNORECASE)


def _color_of(opt) -> str | None:
    """옵션 문자열 → 컬러(없으면 None=사이즈만 → 컬러는 UID 레벨)."""
    if not opt or not isinstance(opt, str):
        return None
    if "^" in opt:
        c = opt.split("^", 1)[0].strip()
        return c or None
    if "/" in opt:
        left = opt.split("/", 1)[0].strip()
        if left and not _SIZE_RE.match(left):
            return left
    return None


def _size_of(opt) -> str:
    """옵션 문자열 → 사이즈(컬러의 반대편). '^'뒤 / (컬러/사이즈면)'/'뒤 / 그 외 전체(사이즈만)."""
    if not opt or not isinstance(opt, str):
        return "(무옵션)"
    if "^" in opt:
        return opt.split("^", 1)[1].strip() or "(무옵션)"
    if "/" in opt:
        left = opt.split("/", 1)[0].strip()
        if left and not _SIZE_RE.match(left):
            return opt.split("/", 1)[1].strip() or "(무옵션)"
    return opt.strip() or "(무옵션)"


def _add_color_keys(df):
    """df(barcode 단위)에 __color_key(=goods_no|컬러 또는 UID:goods_no)·__opt_color·__size 부여."""
    gn = df["goods_no"].fillna(0).astype("int64")
    colors = df["goods_opt"].map(_color_of)
    df["__color_key"] = [f"{g}|{c}" if c else f"UID:{g}" for g, c in zip(gn, colors)]
    df["__opt_color"] = colors.notna().astype(int)
    df["__size"] = df["goods_opt"].map(_size_of)
    return df


# 브로큰 SKU 정의: 사이즈 3개 이상 보유한 컬러-SKU 중, 구색률(해당 stock 사이즈수 ÷ 전체 보유 사이즈수) < 임계.
_BROKEN_FILL = 0.5
_BROKEN_MIN_SIZES = 3


def _broken_keys(df, stock_mask) -> set:
    """stock_mask(그 위치 점재고>0 bool)에서 '브로큰'인 컬러-SKU key 집합.
       n_all = df 전체(재고 어디든) 그 컬러의 distinct 사이즈, n_stk = stock_mask 사이즈. n_all>=3 & n_stk/n_all<임계."""
    n_all = df.groupby("__color_key")["__size"].nunique()
    sub = df[stock_mask]
    if sub.empty:
        return set()
    n_stk = sub.groupby("__color_key")["__size"].nunique()
    j = pd.DataFrame({"n_all": n_all, "n_stk": n_stk}).dropna(subset=["n_stk"])
    br = j[(j["n_all"] >= _BROKEN_MIN_SIZES) & (j["n_stk"] / j["n_all"] < _BROKEN_FILL)]
    return set(br.index)


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
    if f.get("concept") and "concept" in df.columns:   # 마케팅 컨셉 필터
        df = df[df["concept"].isin(f["concept"])]
    if f.get("md") and "off_md_id" in df.columns:
        df = df[df["off_md_id"].isin(f["md"])]
    if f.get("goods"):
        gset = {str(x) for x in f["goods"]}
        df = df[df["goods_no"].astype(str).isin(gset)]
    if f.get("name_like"):                       # 상품명 부분일치(대소문자 무시) — 예: ACG
        nl = str(f["name_like"]).strip().lower()
        if nl:
            df = df[df["goods_nm"].astype(str).str.lower().str.contains(nl, na=False, regex=False)]
    if f.get("running") and "is_running" in df.columns:   # 러닝화만(RUN 매장 취급 신발)
        df = df[df["is_running"] == 1]
    df = df[df["goods_nm"].astype(str).str.strip() != ""].copy()
    df["__jaego"] = df[vis].sum(axis=1) if vis else 0
    df["__hub"] = df[hcol].fillna(0) if hcol else 0
    df = df[(df["__jaego"] > 0) | (df["__hub"] > 0)]
    df = _add_color_keys(df)
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


def _brand_stock(df, vis, top_n=None):
    """브랜드별 점재고(선택 매장) — 사업구분(위탁/매입/기타) 스택 + 전체 대비 비중. 전체 브랜드(top_n=None).
       매장별 재고수량 차트와 동일 스키마({name,위탁,매입,기타,total}) + share + 컬러SKU/바코드SKU."""
    if not vis or df.empty:
        return []
    grand = float(df["__jaego"].sum()) or 0.0
    piv = df.groupby(["brand_nm", "business_type"])["__jaego"].sum().unstack(fill_value=0.0)
    ins = df[df["__jaego"] > 0]               # 컬러/바코드 SKU는 점재고 보유(매장 내) 기준
    sku = ins.groupby("brand_nm").agg(color_sku=("__color_key", "nunique"),
                                      barcode_sku=("barcode", "nunique"),
                                      uid=("goods_no", "nunique"))
    bkeys = _broken_keys(df, df["__jaego"] > 0)   # 선택매장 점재고 기준 브로큰 컬러-SKU
    brk = ins[ins["__color_key"].isin(bkeys)].groupby("brand_nm")["__color_key"].nunique()
    rows = []
    for brand, row in piv.iterrows():
        wt = float(row.get("위탁", 0.0)); mi = float(row.get("매입", 0.0))
        etc = float(row.sum()) - wt - mi          # 기타 + 그 외 사업구분
        total = wt + mi + etc
        if total <= 0:
            continue
        s = sku.loc[brand] if brand in sku.index else None
        rows.append({"name": (str(brand) or "(미매칭)"), "위탁": _f(wt), "매입": _f(mi), "기타": _f(etc),
                     "total": _f(total), "share": round(total / grand * 100, 1) if grand else 0.0,
                     "color_sku": int(s["color_sku"]) if s is not None else 0,
                     "barcode_sku": int(s["barcode_sku"]) if s is not None else 0,
                     "uid": int(s["uid"]) if s is not None else 0,
                     "broken_sku": int(brk.get(brand, 0))})
    rows.sort(key=lambda x: -x["total"])
    return rows if top_n is None else rows[:top_n]


def brand_csv_rows(f=None):
    """브랜드별 점재고 CSV — **매장 분리(long)**: (브랜드 × 매장)당 1행.
       열 = 브랜드·사업구분·매장 + 점재고수량·UID수·컬러SKU·바코드SKU. 재고 0인 (브랜드×매장)은 제외."""
    df, vis, hubcols, hcol = _prep(f)
    header = ["브랜드", "사업구분", "매장", "점재고수량", "UID수", "컬러SKU", "바코드SKU", "브로큰SKU"]
    if df.empty or not vis:
        return header, []
    out = []
    for s in vis:                                   # 각 매장 컬럼(그 매장 점재고 보유 행)별 브랜드 집계
        m = df[s].fillna(0) > 0
        sub = df[m]
        if sub.empty:
            continue
        bkeys = _broken_keys(df, m)                  # 그 매장 구색률 기준 브로큰 컬러-SKU
        subb = sub.assign(__broken=sub["__color_key"].isin(bkeys))
        agg = subb.groupby(["brand_nm", "business_type"]).agg(
            qty=(s, "sum"), uid=("goods_no", "nunique"),
            color=("__color_key", "nunique"), bc=("barcode", "nunique"))
        brk = subb[subb["__broken"]].groupby(["brand_nm", "business_type"])["__color_key"].nunique()
        for (brand, biz), r in agg.iterrows():
            q = int(_f(r["qty"]))
            if q <= 0:
                continue
            out.append([str(brand) or "(미매칭)", str(biz), s, q,
                        int(r["uid"]), int(r["color"]), int(r["bc"]), int(brk.get((brand, biz), 0))])
    out.sort(key=lambda x: (x[0], -x[3]))            # 브랜드명, 매장 점재고 내림차순
    return header, out


def _cat_sku(df, top_n=12):
    """카테고리별(최상위/대/중) **점재고 매장 내** 컬러SKU/바코드SKU — 표용. 상위 top_n + '기타'."""
    ins = df[df["__jaego"] > 0]
    bkeys = _broken_keys(df, df["__jaego"] > 0)      # 선택매장 점재고 기준 브로큰 컬러-SKU
    ins = ins.assign(__broken=ins["__color_key"].isin(bkeys))
    out = {}
    for key in ("cat_top", "cat_large", "cat_medium"):
        if key not in ins.columns or ins.empty:
            out[key] = []
            continue
        cat = ins[key].astype(str).str.strip().replace("", "(미분류)").fillna("(미분류)")
        tmp = ins.assign(_c=cat)
        g = tmp.groupby("_c").agg(color_sku=("__color_key", "nunique"),
                                  barcode_sku=("barcode", "nunique"),
                                  uid=("goods_no", "nunique"))
        brk = tmp[tmp["__broken"]].groupby("_c")["__color_key"].nunique()
        g["broken_sku"] = [int(brk.get(k, 0)) for k in g.index]
        g = g.sort_values("color_sku", ascending=False)
        items = [{"name": str(k), "color_sku": int(r.color_sku), "barcode_sku": int(r.barcode_sku),
                  "uid": int(r.uid), "broken_sku": int(r.broken_sku)} for k, r in g.iterrows()]
        if len(items) > top_n:
            head, tail = items[:top_n], items[top_n:]
            head.append({"name": f"기타 {len(tail)}종",
                         "color_sku": sum(x["color_sku"] for x in tail),
                         "barcode_sku": sum(x["barcode_sku"] for x in tail),
                         "uid": sum(x["uid"] for x in tail),
                         "broken_sku": sum(x["broken_sku"] for x in tail)})
            items = head
        out[key] = items
    return out


def _store_sku(df, vis):
    """매장별 컬러SKU/바코드SKU/UID/브로큰SKU — 각 매장 컬럼>0(그 매장 점재고 보유)인 행 기준.
       브로큰은 그 매장 내 구색률 기준(사이즈 3+ & 매장 사이즈/전체 사이즈<임계)."""
    rows = []
    for s in vis:
        m = df[s].fillna(0) > 0
        sub = df[m]
        if sub.empty:
            continue
        bkeys = _broken_keys(df, m)
        rows.append({"name": s,
                     "color_sku": int(sub["__color_key"].nunique()),
                     "barcode_sku": int(sub["barcode"].nunique()),
                     "uid": int(sub["goods_no"].nunique()),
                     "broken_sku": int(len(bkeys))})
    rows.sort(key=lambda r: -r["color_sku"])
    return rows


def compute(f=None, limit=300):
    df, vis, hubcols, hcol = _prep(f)
    if df.empty:
        return {"empty": True, "kpis": {"jaego": 0, "hub": 0, "options": 0, "goods": 0, "color_sku": 0},
                "stores": [], "rows": [], "store_cols": [], "hubcols": hubcols, "cats": {},
                "brand_stock": [], "store_sku": [], "cat_sku": {}}

    # KPI 카운트는 goods/options와 같은 base(재고 보유=점 or 허브)로 통일 → goods ≤ 컬러SKU ≤ barcode 계층 일관.
    # (매장별/브랜드/카테 표의 SKU는 '점재고 보유' 기준 — _store_sku/_brand_stock/_cat_sku 참고)
    kpis = {"jaego": _f(df["__jaego"].sum()), "hub": _f(df["__hub"].sum()),
            "options": int(len(df)), "goods": int(df["goods_no"].nunique()),
            "color_sku": int(df["__color_key"].nunique())}
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
    # 브로큰 여부(컬러-SKU 단위, 선택매장 점재고 기준) — 옵션 행마다 그 상품컬러가 브로큰이면 'Y'.
    _bkeys = _broken_keys(df, df["__jaego"] > 0)
    df["브로큰"] = ["Y" if k in _bkeys else "" for k in df["__color_key"]]
    idcols = [c for c in ("brand_nm", "goods_nm", "goods_no", "goods_opt", "business_type",
                          "cat_top", "cat_large", "cat_medium", "브로큰") if c in df.columns]
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
            "brand_stock": _brand_stock(df, vis),
            "store_sku": _store_sku(df, vis), "cat_sku": _cat_sku(df)}


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
