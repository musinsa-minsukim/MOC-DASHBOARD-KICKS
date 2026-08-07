"""상품 메타 enrichment — 스타일넘버·현재가(정상가/판매가)·점별 재고.

- 현재가: goods_master(bizest.goods) normal_price/sale_price (sale_price 0=할인없음→정상가).
- 점별 재고: inventory_pivot을 goods_no/brand_nm × 매장으로 1회 피벗 후 캐시(mtime 무효화).
- 매장 컬럼은 invtab._cols로 탐색(한글 매장명).
"""
from __future__ import annotations

import os
import math

import pandas as pd

import store
import invtab

_pv: dict = {"mtime": None, "by_goods": None, "by_brand": None, "store_cols": None}


def _f(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(x) or math.isinf(x) else x


def _ensure():
    m = os.path.getmtime(store._path("inventory_pivot"))
    if _pv["mtime"] != m:
        inv = store.get_inventory_pivot()
        sc, _, _, _ = invtab._cols(inv.columns)
        tmp = inv.assign(_gid=pd.to_numeric(inv["goods_no"], errors="coerce")).dropna(subset=["_gid"])
        tmp["_gid"] = tmp["_gid"].astype("int64")
        _pv["by_goods"] = tmp.groupby("_gid")[sc].sum()
        _pv["by_brand"] = inv.groupby("brand_nm")[sc].sum()
        _pv["store_cols"] = sc
        _pv["mtime"] = m
    return _pv


def store_columns():
    return list(_ensure()["store_cols"])


def stock_by_goods(goods_nos):
    """{goods_no: {store_name: qty, ...}} (점별 재고, 선택 goods만)."""
    g = _ensure()["by_goods"]
    out = {}
    for gid in {int(x) for x in goods_nos}:
        if gid in g.index:
            out[gid] = {s: _f(v) for s, v in g.loc[gid].items()}
    return out


def stock_by_brand(brands):
    """{brand_nm: {store_name: qty, ...}} (브랜드별 점별 재고 합)."""
    g = _ensure()["by_brand"]
    out = {}
    for b in set(brands):
        if b in g.index:
            out[b] = {s: _f(v) for s, v in g.loc[b].items()}
    return out


def goods_catalog(goods_nos):
    """{goods_no: {style_no, normal_price, sale_price(유효)}} — goods_master 현재가, 선택 goods만 스코프 조회."""
    ids = list({int(x) for x in goods_nos})
    if not ids:
        return {}
    out = {}
    # IN 리스트가 매우 크면 분할
    for i in range(0, len(ids), 2000):
        chunk = ids[i:i + 2000]
        ph = ",".join(["?"] * len(chunk))
        df = store.query(
            f"SELECT goods_no, style_no, normal_price, sale_price FROM goods_master WHERE goods_no IN ({ph})",
            chunk)
        for r in df.itertuples(index=False):
            npv, spv = _f(r.normal_price), _f(r.sale_price)
            out[int(r.goods_no)] = {"style_no": (r.style_no or ""),
                                    "normal_price": npv,
                                    "sale_price": spv if spv > 0 else npv}  # 0=할인없음→정상가
    return out
