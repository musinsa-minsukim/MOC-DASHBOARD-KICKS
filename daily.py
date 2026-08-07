"""요약(전일 일별 리포트) 계산 — Streamlit 요약 탭의 React 이식.

- 필터: 최상위 카테(basis) + 사업구분(seg=매입/위탁) → 리포트 전체가 그 기준으로 재계산.
  (사이드바 분석 필터와는 무관 — 요약 자체의 기준 선택자.)
- 기준일 d0 = (필터된) 최신 데이터일. 최근 4일(d0~d3)로 추이/주목상품 계산.
- 매출 집계: store.query(DuckDB, parquet, ASCII 컬럼). ⚠️ name은 DuckDB 예약어 → AS "name".
- 재고 조인: pandas. 한글 컬럼은 이름 패턴으로 탐색(합계/1000/1700/MFS) — 위치 폴백 포함.
- 캐시: (sales/inventory mtime, basis, seg) 키 → 데이터 갱신 또는 기준 변경 때만 재계산.

원본(app.py 요약 탭) 로직 충실 복제:
  · 액션포인트: 매장 전일 vs 직전일 판매수량(직전일≥3) 급등/급락 + 긴급보충 1순위.
  · 주목상품: 🔥판매TOP(d0 상위5) ∪ 📈급등(ratio상위5,직전일≥2) ∪ 📉급락(하위5) + 4일 수량 + 점재고/허브1000/1700/MFS.
  · 재고보충: (매장×상품) 전일판매>0 & 허브합계>0 & 점재고<전일판매×2, 점재고 오름차순 → 긴급=점재고≤0.
  · 브랜드/상품 각 TOP100.
"""
from __future__ import annotations

import os
import math

import store

_cache: dict = {}   # (mtime, basis, seg) -> payload


def _mtime(name: str) -> float:
    p = store._path(name)
    return os.path.getmtime(p) if os.path.exists(p) else 0.0


def _f(v) -> float:
    """None/NaN/Inf → 0.0 (NaN은 invalid JSON)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(x) or math.isinf(x) else x


def _pct(cur: float, prev: float):
    if not prev:
        return None
    return (cur / prev - 1) * 100


def _filter_sql(basis: str | None, seg: str | None):
    """basis(cat_top)·seg(business_type) → 추가 WHERE 조각 + 파라미터."""
    cl, params = [], []
    if basis and basis != "전체":
        cl.append("cat_top = ?"); params.append(basis)
    if seg and seg != "전체":
        cl.append("business_type = ?"); params.append(seg)
    return (" AND " + " AND ".join(cl)) if cl else "", params


def _inv_cols(cols: list[str]) -> dict:
    """재고 컬럼 탐색: 점재고합계 / 허브합계 / 허브1000 / 허브1700 / MFS."""
    cols = [str(c) for c in cols]
    totals = [c for c in cols if c.endswith("합계")]
    d = {
        "store_stock": next((c for c in totals if "점재고" in c), cols[17] if len(cols) > 17 else None),
        "hub_total": next((c for c in totals if "허브" in c), cols[18] if len(cols) > 18 else None),
        "hub1000": next((c for c in cols if "1000" in c), None),
        "hub1700": next((c for c in cols if "1700" in c), None),
        "mfs": next((c for c in cols if c == "MFS"), None),
    }
    return d


def _store_stock_col(cols: list[str]) -> str:
    """inventory_store_long의 점재고 컬럼(= goods_no, store_name 외 나머지)."""
    rest = [str(c) for c in cols if str(c) not in ("goods_no", "store_name")]
    return rest[0] if rest else cols[2]


def compute(basis: str | None, seg: str | None) -> dict:
    fsql, fp = _filter_sql(basis, seg)

    today = store.today_kst()  # 오늘(KST) 제외 → d0는 항상 어제(완성된 날). '전일 리포트' 의도 고정.
    dates = store.query(
        f"SELECT DISTINCT CAST(sales_date AS DATE) d FROM sales WHERE 1=1{fsql} "
        f"AND CAST(sales_date AS DATE) < ? ORDER BY d DESC LIMIT 21", [*fp, today])["d"].tolist()
    if not dates:
        return {"empty": True, "basis": basis or "전체", "seg": seg or "전체"}

    ds = [str(x)[:10] for x in dates]
    d0 = ds[0]
    d4 = ds[:4]                       # [d0, d1, d2, d3]
    dnames = ["전일", "-2일", "-3일", "-4일"][:len(d4)]
    d1 = d4[1] if len(d4) > 1 else None

    # ---- d0 종합 ----
    tot = store.query(
        f"""SELECT CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(qty) AS DOUBLE) qty,
                   CAST(sum(foreign_gmv) AS DOUBLE) fgn, count(DISTINCT goods_no) goods,
                   count(DISTINCT store_name) stores
            FROM sales WHERE CAST(sales_date AS DATE)=?{fsql}""", [d0] + fp).iloc[0]
    gmv0, qty0 = _f(tot.gmv), _f(tot.qty)
    gmv1 = 0.0
    if d1:
        gmv1 = _f(store.query(
            f"SELECT CAST(sum(gmv) AS DOUBLE) gmv FROM sales WHERE CAST(sales_date AS DATE)=?{fsql}",
            [d1] + fp).iloc[0].gmv)
    wow = _pct(gmv0, gmv1)

    # ---- 4일 / 14일 추이 ----
    lo14 = ds[min(len(ds) - 1, 13)]
    tr = store.query(
        f"""SELECT CAST(sales_date AS DATE) d, CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(qty) AS DOUBLE) qty
            FROM sales WHERE CAST(sales_date AS DATE) >= ?{fsql} GROUP BY 1 ORDER BY 1""", [lo14] + fp)
    tr["d"] = tr["d"].astype(str).str[:10]
    trend = [{"date": r.d, "gmv": _f(r.gmv), "qty": _f(r.qty)} for r in tr.itertuples()]
    tmap = {r["date"]: r for r in trend}
    trend4 = [{"label": dnames[i], "date": d4[i],
               "gmv": _f(tmap.get(d4[i], {}).get("gmv", 0)),
               "qty": _f(tmap.get(d4[i], {}).get("qty", 0))} for i in range(len(d4))]

    # ---- 매장별(d0) ----
    st = store.query(
        f"""SELECT store_name AS "name", any_value(shop_type) shop_type,
                   CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(qty) AS DOUBLE) qty
            FROM sales WHERE CAST(sales_date AS DATE)=?{fsql} GROUP BY store_name
            ORDER BY gmv DESC""", [d0] + fp)
    stores = [{"name": r.name, "shop_type": r.shop_type, "gmv": _f(r.gmv), "qty": _f(r.qty),
               "share": (_f(r.gmv) / gmv0 * 100 if gmv0 else 0)} for r in st.itertuples()]

    # ---- 브랜드 TOP100(d0) ----
    br = store.query(
        f"""SELECT brand_nm AS "name", CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(qty) AS DOUBLE) qty
            FROM sales WHERE CAST(sales_date AS DATE)=?{fsql} GROUP BY 1 ORDER BY gmv DESC LIMIT 100""",
        [d0] + fp)
    brands = [{"name": r.name, "gmv": _f(r.gmv), "qty": _f(r.qty),
               "share": (_f(r.gmv) / gmv0 * 100 if gmv0 else 0)} for r in br.itertuples()]

    # ---- 상품 TOP100(d0) ----
    gd = store.query(
        f"""SELECT goods_no, any_value(goods_nm) nm, any_value(brand_nm) brand,
                   CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(qty) AS DOUBLE) qty
            FROM sales WHERE CAST(sales_date AS DATE)=?{fsql} GROUP BY goods_no
            ORDER BY gmv DESC LIMIT 100""", [d0] + fp)
    goods = [{"goods_no": int(r.goods_no), "name": r.nm, "brand": r.brand,
              "gmv": _f(r.gmv), "qty": _f(r.qty),
              "share": (_f(r.gmv) / gmv0 * 100 if gmv0 else 0)} for r in gd.itertuples()]

    notable, restock, actions = _heavy_sections(d4, dnames, d0, d1, fsql, fp, gmv0)
    cat_brand = _cat_brand_rank(d0, d1, fsql, fp)
    cat_goods = _cat_goods_rank(d0, d1, fsql, fp)

    return {
        "empty": False,
        "basis": basis or "전체", "seg": seg or "전체",
        "latest": d0, "prev": d1,
        "issue": (wow is not None and wow < -5),
        "totals": {
            "gmv": gmv0, "qty": qty0, "foreign_gmv": _f(tot.fgn),
            "goods": int(_f(tot.goods)), "stores": int(_f(tot.stores)),
            "gmv_prev": gmv1, "gmv_delta": wow,
            "foreign_ratio": (_f(tot.fgn) / gmv0 * 100 if gmv0 else 0),
        },
        "lead_store": stores[0] if stores else None,
        "lead_brand": brands[0] if brands else None,
        "trend": trend, "trend4": trend4, "dnames": dnames,
        "stores": stores, "brands": brands, "goods": goods,
        "notable": notable, "restock": restock, "actions": actions,
        "cat_brand": cat_brand, "cat_goods": cat_goods,
    }


def _restock_df(d0, fsql, fp):
    """재고보충 후보 (매장×상품) — 필터·정렬까지 끝낸 전체 DataFrame. JSON(상위 N)·CSV(전체) 공용."""
    import pandas as pd
    sd = store.query(
        f"""SELECT store_name, goods_no, any_value(goods_nm) goods_nm,
                   any_value(brand_nm) brand_nm, CAST(sum(qty) AS DOUBLE) sold
            FROM sales WHERE CAST(sales_date AS DATE)=?{fsql}
            GROUP BY store_name, goods_no HAVING sum(qty) > 0""", [d0] + fp)
    cols = ["store_name", "goods_no", "goods_nm", "brand_nm", "sold", "store_stock", "hub_total"]
    if sd.empty:
        return pd.DataFrame(columns=cols)
    inv = store.get_inventory_goods()
    ic = _inv_cols(list(inv.columns))
    hub = inv[["goods_no", ic["hub_total"]]].rename(columns={ic["hub_total"]: "hub_total"})
    isl = store.get_inventory_store_long()
    sc = _store_stock_col(list(isl.columns))
    isl = isl[["store_name", "goods_no", sc]].rename(columns={sc: "store_stock"})
    m = sd.merge(isl, on=["store_name", "goods_no"], how="left").merge(hub, on="goods_no", how="left")
    m["store_stock"] = m["store_stock"].fillna(0)
    m["hub_total"] = m["hub_total"].fillna(0)
    m = m[(m["sold"] > 0) & (m["hub_total"] > 0) & (m["store_stock"] < m["sold"] * 2)]
    return m.sort_values(["store_stock", "sold"], ascending=[True, False])


def restock_full(basis: str | None = None, seg: str | None = None):
    """재고보충 전체 행(캡 없음) — CSV 다운로드용. (d0, rows[])."""
    fsql, fp = _filter_sql(basis or "전체", seg or "전체")
    dd = store.query(f"SELECT max(CAST(sales_date AS DATE)) d FROM sales WHERE 1=1{fsql} "
                     f"AND CAST(sales_date AS DATE) < ?", [*fp, store.today_kst()]).iloc[0].d
    if dd is None:
        return None, []
    d0 = str(dd)[:10]
    m = _restock_df(d0, fsql, fp)
    rows = [{"store": r.store_name, "brand": r.brand_nm, "goods_no": int(r.goods_no),
             "name": r.goods_nm, "sold": _f(r.sold), "store_stock": _f(r.store_stock),
             "hub_total": _f(r.hub_total)} for r in m.itertuples()]
    return d0, rows


def _cat_brand_rank(d0, d1, fsql, fp, top_cats=10, top_brands=5):
    """중카테고리별 브랜드 랭킹 (전일 GMV 상위, 중카테 내 비중, 전일 대비 신장율). #6"""
    d1q = d1 or d0
    df = store.query(
        f"""SELECT cat_medium, brand_nm,
                   CAST(sum(CASE WHEN CAST(sales_date AS DATE)=? THEN gmv ELSE 0 END) AS DOUBLE) g0,
                   CAST(sum(CASE WHEN CAST(sales_date AS DATE)=? THEN gmv ELSE 0 END) AS DOUBLE) g1
            FROM sales WHERE CAST(sales_date AS DATE) IN (?, ?){fsql}
            GROUP BY cat_medium, brand_nm""", [d0, d1q, d0, d1q] + fp)
    if df.empty:
        return []
    df = df[df["g0"] > 0]
    if df.empty:
        return []
    out = []
    cat_tot = df.groupby("cat_medium")["g0"].sum().sort_values(ascending=False)
    for cat in cat_tot.head(top_cats).index:
        total = _f(cat_tot[cat])
        sub = df[df["cat_medium"] == cat].sort_values("g0", ascending=False).head(top_brands)
        brands = []
        for r in sub.itertuples(index=False):
            g0, g1 = _f(r.g0), _f(r.g1)
            brands.append({"name": r.brand_nm, "gmv": g0,
                           "share": (g0 / total * 100 if total else 0),
                           "delta": _pct(g0, g1)})
        out.append({"cat": cat, "total": total, "brands": brands})
    return out


def _cat_goods_rank(d0, d1, fsql, fp, top_cats=10, top_goods=10):
    """중카테고리별 상품 랭킹 (전일 GMV 상위 TOP10, 중카테 내 비중, 전일 대비 신장율). #1"""
    d1q = d1 or d0
    df = store.query(
        f"""SELECT cat_medium, goods_no, any_value(goods_nm) nm, any_value(brand_nm) brand,
                   CAST(sum(CASE WHEN CAST(sales_date AS DATE)=? THEN gmv ELSE 0 END) AS DOUBLE) g0,
                   CAST(sum(CASE WHEN CAST(sales_date AS DATE)=? THEN gmv ELSE 0 END) AS DOUBLE) g1
            FROM sales WHERE CAST(sales_date AS DATE) IN (?, ?){fsql}
            GROUP BY cat_medium, goods_no""", [d0, d1q, d0, d1q] + fp)
    if df.empty:
        return []
    df = df[df["g0"] > 0]
    if df.empty:
        return []
    out = []
    cat_tot = df.groupby("cat_medium")["g0"].sum().sort_values(ascending=False)
    for cat in cat_tot.head(top_cats).index:
        total = _f(cat_tot[cat])
        sub = df[df["cat_medium"] == cat].sort_values("g0", ascending=False).head(top_goods)
        goods = []
        for r in sub.itertuples(index=False):
            g0, g1 = _f(r.g0), _f(r.g1)
            goods.append({"goods_no": int(r.goods_no), "name": r.nm, "brand": r.brand, "gmv": g0,
                          "share": (g0 / total * 100 if total else 0),
                          "delta": _pct(g0, g1)})
        out.append({"cat": cat, "total": total, "goods": goods})
    return out


def _heavy_sections(d4, dnames, d0, d1, fsql, fp, gmv0):
    """주목상품(4일)·재고보충(매장×상품)·액션포인트 — 재고 parquet 조인 포함."""
    import pandas as pd

    # ===== 주목 상품: goods_no × 4일 수량 =====
    case_cols = ", ".join(
        f"CAST(sum(CASE WHEN CAST(sales_date AS DATE)='{d4[i]}' THEN qty ELSE 0 END) AS DOUBLE) q{i}"
        for i in range(len(d4)))
    g = store.query(
        f"""SELECT goods_no, any_value(goods_nm) nm, any_value(brand_nm) brand, {case_cols}
            FROM sales WHERE CAST(sales_date AS DATE) IN ({','.join(['?'] * len(d4))}){fsql}
            GROUP BY goods_no""", d4 + fp)

    inv = store.get_inventory_goods()
    ic = _inv_cols(list(inv.columns))
    keep = ["goods_no"] + [ic[k] for k in ("store_stock", "hub1000", "hub1700", "mfs") if ic.get(k)]
    inv_small = inv[keep].rename(columns={
        ic["store_stock"]: "store_stock", ic.get("hub1000"): "hub1000",
        ic.get("hub1700"): "hub1700", ic.get("mfs"): "mfs"})

    notable = []
    if not g.empty:
        tag: dict = {}
        top5 = g.sort_values("q0", ascending=False).head(5)["goods_no"].tolist()
        for gn in top5:
            tag[gn] = {"kind": "판매TOP", "pct": None}
        if d1 is not None and "q1" in g.columns:
            rr = g[g["q1"] >= 2].copy()
            if not rr.empty:
                rr["gr"] = (rr["q0"] - rr["q1"]) / rr["q1"] * 100
                for r in rr.sort_values("gr", ascending=False).head(5).itertuples():
                    tag.setdefault(int(r.goods_no), {"kind": "급등", "pct": _f(r.gr)})
                for r in rr.sort_values("gr").head(5).itertuples():
                    tag.setdefault(int(r.goods_no), {"kind": "급락", "pct": _f(r.gr)})
        gi = g.set_index("goods_no")
        gi = gi.join(inv_small.set_index("goods_no"))
        order = {"판매TOP": 0, "급등": 1, "급락": 2}
        for gn in sorted(tag.keys(), key=lambda x: (order[tag[x]["kind"]], -_f(gi.loc[x, "q0"]) if x in gi.index else 0)):
            if gn not in gi.index:
                continue
            row = gi.loc[gn]
            notable.append({
                "goods_no": int(gn), "name": row.get("nm", ""), "brand": row.get("brand", ""),
                "tag": tag[gn]["kind"], "tag_pct": tag[gn]["pct"],
                "days": [_f(row.get(f"q{i}", 0)) for i in range(len(d4))],
                "store_stock": _f(row.get("store_stock", 0)),
                "hub1000": _f(row.get("hub1000", 0)),
                "hub1700": _f(row.get("hub1700", 0)),
                "mfs": _f(row.get("mfs", 0)),
            })

    # ===== 재고보충: (매장×상품) — 전체 계산은 _restock_df로 일원화(CSV 전체와 동일) =====
    m = _restock_df(d0, fsql, fp)
    restock_count = int(len(m))
    urgent = m[m["store_stock"] <= 0]
    actions_urgent = {"count": int(len(urgent)), "first": None}
    if len(urgent):
        u = urgent.iloc[0]
        actions_urgent["first"] = {
            "store": u["store_name"], "brand": u["brand_nm"],
            "goods_nm": str(u["goods_nm"])[:20], "sold": _f(u["sold"])}
    restock = [{
        "store": r.store_name, "brand": r.brand_nm, "goods_no": int(r.goods_no),
        "name": r.goods_nm, "sold": _f(r.sold), "store_stock": _f(r.store_stock),
        "hub_total": _f(r.hub_total), "urgent": bool(r.store_stock <= 0),
    } for r in m.head(3000).itertuples()]

    # ===== 액션포인트: 매장 전일 vs 직전일 판매수량 =====
    spike = {"up": None, "down": None}
    if d1 is not None:
        sq = store.query(
            f"""SELECT store_name,
                       CAST(sum(CASE WHEN CAST(sales_date AS DATE)=? THEN qty ELSE 0 END) AS DOUBLE) q0,
                       CAST(sum(CASE WHEN CAST(sales_date AS DATE)=? THEN qty ELSE 0 END) AS DOUBLE) q1
                FROM sales WHERE CAST(sales_date AS DATE) IN (?, ?){fsql}
                GROUP BY store_name""", [d0, d1, d0, d1] + fp)
        sq = sq[sq["q1"] >= 3].copy()
        if not sq.empty:
            sq["pct"] = (sq["q0"] - sq["q1"]) / sq["q1"] * 100
            up = sq.sort_values("pct", ascending=False).iloc[0]
            dn = sq.sort_values("pct").iloc[0]
            spike["up"] = {"store": up["store_name"], "pct": _f(up["pct"])}
            spike["down"] = {"store": dn["store_name"], "pct": _f(dn["pct"])}

    actions = {
        "spike_up": spike["up"], "spike_down": spike["down"],
        "urgent_count": actions_urgent["count"], "urgent_first": actions_urgent["first"],
        "restock_count": restock_count,
    }
    return notable, restock, actions


def report(basis: str | None = None, seg: str | None = None) -> dict:
    basis = basis or "전체"
    seg = seg or "전체"
    k = (_mtime("sales"), _mtime("inventory_goods"), _mtime("inventory_store_long"), basis, seg)
    if k not in _cache:
        if len(_cache) > 64:
            _cache.clear()
        _cache[k] = compute(basis, seg)
    return _cache[k]
