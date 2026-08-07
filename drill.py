"""드릴다운 + long CSV export 엔진 (v2 신규).

매장 → 브랜드 → 대카테 → 중카테 → 상품 계층을 sales/inventory 캐시에서 조회.
- 화면은 피벗(wide)일 수 있으나, **CSV export는 tidy/long**(엔티티당 1행, 지표=열).
- api.py 가 get_filters(f)를 넘겨주면 여기서 sales/inventory WHERE를 각각 구성.
- 판매지표는 `sales` 뷰, 점재고는 `inventory_store_long`(goods_no×store×"점재고")에서 조인.
"""
from __future__ import annotations

import io
import csv
import math

import store

# 드릴 레벨: key -> (공통 차원 컬럼, 한글 라벨, get_filters 필터키)
_LEVELS = [
    ("shop",       "store_name", "매장",       "store"),
    ("brand",      "brand_nm",   "브랜드",      "brand"),
    ("cat_large",  "cat_large",  "대카테고리",   "cat_large"),
    ("cat_medium", "cat_medium", "중카테고리",   "cat_medium"),
    ("goods",      "goods_no",   "상품UID",     "goods"),
]
LEVEL_COL    = {k: c for k, c, _, _ in _LEVELS}
LEVEL_LABEL  = {k: l for k, _, l, _ in _LEVELS}
LEVEL_FILTER = {k: fk for k, _, _, fk in _LEVELS}

# sales WHERE 용 IN 컬럼 (get_filters 키 -> sales 컬럼)
_SALES_IN = {"biz": "business_type", "type": "shop_type", "store": "store_name",
             "brand": "brand_nm", "cat_top": "cat_top", "cat_large": "cat_large",
             "cat_medium": "cat_medium", "md": "off_md_id"}

# inventory 차원(goods_no→차원 매핑; inventory_store_long엔 store만 있어 sales에서 조인)
_INV_DIM = ("(SELECT DISTINCT goods_no, brand_nm, cat_top, cat_large, cat_medium, "
            "business_type FROM sales)")
_INV_IN = {"biz": "d.business_type", "store": "i.store_name", "brand": "d.brand_nm",
           "cat_top": "d.cat_top", "cat_large": "d.cat_large", "cat_medium": "d.cat_medium"}


def _num(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(x) or math.isinf(x) else x


def _sales_where(f: dict):
    clauses, params = [], []
    if f.get("date_from"):
        clauses.append("sales_date >= CAST(? AS DATE)"); params.append(f["date_from"])
    if f.get("date_to"):
        clauses.append("sales_date < CAST(? AS DATE) + INTERVAL 1 DAY"); params.append(f["date_to"])
    for key, col in _SALES_IN.items():
        vals = f.get(key)
        if vals:
            clauses.append(f"{col} IN ({','.join(['?'] * len(vals))})"); params += list(vals)
    if f.get("goods"):
        g = [int(x) for x in f["goods"]]
        clauses.append(f"goods_no IN ({','.join(['?'] * len(g))})"); params += g
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _inv_where(f: dict):
    """재고 WHERE — 날짜/MD/매장타입 제외(스냅샷이라 없음). 매장·브랜드·카테·사업·goods만."""
    clauses, params = [], []
    for key, col in _INV_IN.items():
        vals = f.get(key)
        if vals:
            clauses.append(f"{col} IN ({','.join(['?'] * len(vals))})"); params += list(vals)
    if f.get("goods"):
        g = [int(x) for x in f["goods"]]
        clauses.append(f"i.goods_no IN ({','.join(['?'] * len(g))})"); params += g
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _stock_by(level: str, f: dict) -> dict:
    """현재 레벨 키 -> 점재고 합계. 실패 시 빈 dict(재고 캐시 없어도 판매는 반환)."""
    grp = {"shop": "i.store_name", "goods": "i.goods_no"}.get(level, "d." + LEVEL_COL[level])
    iw, ip = _inv_where(f)
    try:
        idf = store.query(
            f'SELECT {grp} AS name, CAST(sum(i."점재고") AS DOUBLE) stock '
            f"FROM inventory_store_long i LEFT JOIN {_INV_DIM} d ON i.goods_no = d.goods_no"
            f"{iw} GROUP BY 1", ip)
    except Exception:
        return {}
    return {r.name: _num(r.stock) for r in idf.itertuples()}


def rows(level: str, f: dict, limit: int | None = None) -> dict:
    """레벨별 판매지표 + 점재고. name=표시명, key=조인/필터값(goods는 goods_no)."""
    col = LEVEL_COL[level]
    sw, sp = _sales_where(f)
    extra = ", any_value(goods_nm) goods_nm" if level == "goods" else ""
    sdf = store.query(
        f"SELECT {col} AS name{extra}, "
        "CAST(sum(qty) AS DOUBLE) qty, CAST(sum(gmv) AS DOUBLE) gmv, "
        "CAST(sum(normal_amt) AS DOUBLE) normal_amt, CAST(sum(foreign_gmv) AS DOUBLE) foreign_gmv, "
        "count(DISTINCT goods_no) goods_count "
        f"FROM sales{sw} GROUP BY 1 ORDER BY gmv DESC"
        f"{f' LIMIT {int(limit)}' if limit else ''}", sp)
    stock = _stock_by(level, f)
    out = []
    for r in sdf.itertuples():
        gmv, normal, fgn = _num(r.gmv), _num(r.normal_amt), _num(r.foreign_gmv)
        key = int(r.name) if level == "goods" else r.name
        row = {
            "key": key,
            "name": (r.goods_nm if level == "goods" else r.name),
            "qty": _num(r.qty), "gmv": gmv, "normal_amt": normal, "foreign_gmv": fgn,
            "goods_count": int(_num(r.goods_count)),
            "stock": stock.get(key, 0),
            "discount_rate": (1 - gmv / normal) * 100 if normal else 0,
            "foreign_ratio": (fgn / gmv * 100) if gmv else 0,
        }
        out.append(row)
    return {"level": level, "label": LEVEL_LABEL[level], "rows": out}


_METRIC_COLS = [("qty", "순판매수량"), ("gmv", "GMV"), ("normal_amt", "정상가매출"),
                ("foreign_gmv", "외국인GMV"), ("goods_count", "상품수"), ("stock", "점재고")]


def _context_cols(level: str, f: dict):
    """상위 단일선택 필터를 CSV 선행 컨텍스트 열로 (예: 매장 드릴 중이면 '매장=성수' 열)."""
    ctx = []
    for key, lab in (("store", "매장"), ("brand", "브랜드"),
                     ("cat_large", "대카테고리"), ("cat_medium", "중카테고리")):
        vals = f.get(key)
        if vals and len(vals) == 1 and key != LEVEL_FILTER[level]:
            ctx.append((lab, vals[0]))
    return ctx


def csv_bytes(level: str, f: dict) -> bytes:
    """tidy/long CSV — 엔티티당 1행, 지표=열. UTF-8 BOM(엑셀 한글). 콤마·통화기호 없는 원시 숫자."""
    data = rows(level, f, limit=None)["rows"]
    ctx = _context_cols(level, f)
    header = ([c[0] for c in ctx] + [LEVEL_LABEL[level]]
              + (["상품명"] if level == "goods" else [])
              + [lab for _, lab in _METRIC_COLS] + ["할인율%", "외국인비중%"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in data:
        keycell = r["key"] if level == "goods" else r["name"]
        line = ([c[1] for c in ctx] + [keycell]
                + ([r["name"]] if level == "goods" else [])
                + [int(r[k]) if k != "goods_count" else r[k] for k, _ in _METRIC_COLS]
                + [round(r["discount_rate"], 1), round(r["foreign_ratio"], 1)])
        w.writerow(line)
    return ("﻿" + buf.getvalue()).encode("utf-8")
