"""비교·신장율 탭 — 기준일(ref) GMV 신장율: 전일비·전주비·전월비·전년비 (모두 동기=같은 경과일 누적).

원본 app.py 비교 탭 충실 복제:
  · 8개 GMV 윈도우(기준일/전일/당주WTD/전주wtd/당월MTD/전월wtd/당년YTD/전년wtd) — 모두 연속 날짜범위라 BETWEEN.
  · 비율: 전일비=기준일/전일, 전주비=당주/전주wtd, 전월비=당월/전월wtd, 전년비=당년/전년wtd.
  · 셀: 유효X→"X", 기준0 & 당기>0 → "신규"(상품은 reg_date 90일 이내일 때만) 아니면 "—", 그 외 "(+/-)x.x%".
  · 차원: 매장 / 카테고리(최상위·대·중 선택) / 브랜드(상위200) / 상품(상위300). 각 표 합계행.
"""
from __future__ import annotations

import math
import datetime as dt

import store

WIN_ORDER = ["기준일gmv", "전일gmv", "당주gmv", "전주wtd", "당월gmv", "전월wtd", "당년gmv", "전년wtd"]
# (라벨, 당기윈도우, 기준윈도우, 유효키)
RATIOS = [("전일비", "기준일gmv", "전일gmv", "dod"), ("전주비", "당주gmv", "전주wtd", "wk"),
          ("전월비", "당월gmv", "전월wtd", "mo"), ("전년비", "당년gmv", "전년wtd", "yr")]
COLS = ["기준일gmv", "전일gmv", "전일비", "당주gmv", "전주wtd", "전주비",
        "당월gmv", "전월wtd", "전월비", "당년gmv", "전년wtd", "전년비"]
CLV = {"최상위": "cat_top", "대카테": "cat_large", "중카테": "cat_medium"}


def _to_date(v) -> dt.date:
    if isinstance(v, dt.datetime):   # pd.Timestamp은 datetime의 서브클래스
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def _f(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(x) or math.isinf(x) else x


def _windows(ref: dt.date, dmin: dt.date, dmax: dt.date):
    td = dt.timedelta
    iso = ref.isocalendar()
    d_dod = ref - td(days=1)
    wk = ref - td(days=iso.weekday - 1)
    mstart = ref.replace(day=1)
    pmstart = (mstart - td(days=1)).replace(day=1)
    prev_last = mstart - td(days=1)
    prev_end = min(pmstart + td(days=(ref - mstart).days), prev_last)
    ystart = dt.date(ref.year, 1, 1)
    W = {
        "기준일gmv": (ref, ref), "전일gmv": (d_dod, d_dod),
        "당주gmv": (wk, ref), "전주wtd": (wk - td(days=7), ref - td(days=7)),
        "당월gmv": (mstart, ref), "전월wtd": (pmstart, prev_end),
        "당년gmv": (ystart, ref), "전년wtd": (ystart - td(days=364), ref - td(days=364)),
    }
    valid = {"dod": dmin <= d_dod <= dmax, "wk": dmin <= wk - td(days=7),
             "mo": dmin <= pmstart, "yr": dmin <= ystart - td(days=364)}
    info = {"iso_year": iso.year, "iso_week": iso.week, "wday": ["월", "화", "수", "목", "금", "토", "일"][iso.weekday - 1],
            "wk_from": str(wk), "m_from": str(mstart), "pm_from": str(pmstart), "pm_to": str(prev_end), "y_from": str(ystart)}
    return W, valid, info


def _pct(c: float, b: float, valid: bool, newok: bool = True) -> str:
    if not valid:
        return "X"
    if b == 0:
        return "신규" if (c > 0 and newok) else "—"
    return f"{(c - b) / b * 100:+.1f}%"


def _winsel(W):
    # 원시 timestamp 컬럼을 [lo, hi+1) 반열림 비교 → per-row CAST 제거 + parquet 통계 푸시다운.
    parts, p = [], []
    for name in WIN_ORDER:
        lo, hi = W[name]
        parts.append(f'CAST(sum(CASE WHEN sales_date >= ? AND sales_date < ? THEN gmv ELSE 0 END) AS DOUBLE) "{name}"')
        p += [str(lo), str(hi + dt.timedelta(days=1))]
    return ",\n".join(parts), p


def _dim(select_keys, group_keys, where, params, W, valid, label_fn, floor, cap=None, recent=None):
    wsel, wp = _winsel(W)
    wand = where.replace(" WHERE ", " AND ", 1) if where else ""  # 날짜하한 뒤에 AND로 붙임
    sql = (f"SELECT {select_keys}, {wsel} FROM sales "
           f"WHERE sales_date >= ?{wand} GROUP BY {group_keys}")
    df = store.query(sql, wp + [floor] + params)
    if df.empty:
        return None
    df = df[df["기준일gmv"] > 0].sort_values("기준일gmv", ascending=False)
    if df.empty:
        return None
    # 합계는 전체(cap 전) 벡터 합으로 먼저 계산 → 이후 행 dict는 상위 cap개만 생성(대량 낭비 방지)
    total = {name: _f(df[name].sum()) for name in WIN_ORDER}
    tot_row = {"_total": True}
    tot_row.update(total)
    for lab, cw, bw, vk in RATIOS:
        tot_row[lab] = _pct(total[cw], total[bw], valid[vk], True)

    if cap:
        df = df.head(cap)
    rows = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        row = label_fn(d)
        for name in WIN_ORDER:
            row[name] = _f(d[name])
        newok = True if recent is None else (int(d["goods_no"]) in recent)
        for lab, cw, bw, vk in RATIOS:
            row[lab] = _pct(_f(d[cw]), _f(d[bw]), valid[vk], newok)
        rows.append(row)
    return {"rows": rows, "total": tot_row}


def compute(ref_str, clv, where, params):
    rng = store.query("SELECT CAST(min(sales_date) AS DATE) lo, CAST(max(sales_date) AS DATE) hi FROM sales").iloc[0]
    dmin, dmax = _to_date(rng.lo), _to_date(rng.hi)
    ref = _to_date(ref_str) if ref_str else dmax
    W, valid, info = _windows(ref, dmin, dmax)
    floor = str(min(lo for lo, _ in W.values()))   # 가장 이른 윈도우 시작(전년 동기) = 스캔 하한

    # 요약(전체 합계)
    wsel, wp = _winsel(W)
    wand = where.replace(" WHERE ", " AND ", 1) if where else ""
    tot = store.query(f"SELECT {wsel} FROM sales WHERE sales_date >= ?{wand}",
                      wp + [floor] + params).iloc[0]
    WT = {name: _f(tot[name]) for name in WIN_ORDER}
    summary = {name: WT[name] for name in WIN_ORDER}
    summary_ratio = {lab: _pct(WT[cw], WT[bw], valid[vk], True) for lab, cw, bw, vk in RATIOS}

    ckey = CLV.get(clv, "cat_large")

    store_t = _dim('store_name AS "name"', "store_name", where, params, W, valid,
                   lambda d: {"name": d["name"]}, floor)
    cat_t = _dim(f'{ckey} AS "name"', ckey, where, params, W, valid,
                 lambda d: {"name": d["name"]}, floor)
    brand_t = _dim('brand_nm AS "name"', "brand_nm", where, params, W, valid,
                   lambda d: {"name": d["name"]}, floor, cap=200)

    # 신규 판정: goods_master 600만 행 전체 dict 대신, 최근 90일 등록 goods_no만 작은 집합으로 조회
    new_start = str(ref - dt.timedelta(days=90))
    recent = set(int(x) for x in store.query(
        "SELECT DISTINCT goods_no FROM goods_master WHERE CAST(reg_date AS DATE) >= ?",
        [new_start])["goods_no"].tolist())
    goods_t = _dim('brand_nm AS "brand", goods_no, any_value(goods_nm) AS "goods_nm", '
                   'any_value(cat_top) AS "cat_top", any_value(cat_medium) AS "cat_medium"',
                   "brand_nm, goods_no", where, params, W, valid,
                   lambda d: {"brand": d["brand"], "goods_no": int(d["goods_no"]),
                              "goods_nm": d["goods_nm"], "cat_top": d["cat_top"], "cat_medium": d["cat_medium"]},
                   floor, cap=300, recent=recent)

    return {
        "ref": str(ref), "clv": clv, "info": info,
        "cols": COLS, "win_order": WIN_ORDER, "ratio_cols": [r[0] for r in RATIOS],
        "summary": summary, "summary_ratio": summary_ratio,
        "store": store_t, "category": cat_t, "brand": brand_t, "goods": goods_t,
    }
