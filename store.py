"""로컬 Parquet 데이터 저장소 (동시 읽기 안전 + 원자적 갱신).

- 판매(sales): 누적 증분(최근 N일만 재적재 — 뒤늦은 환불/주문상태 변경 흡수). 재고/상품/고객: 스냅샷 교체.
- 저장 = cache/*.parquet, 원자적 교체(tmp 후 os.replace) → 읽는 도중 깨지지 않음.
- 읽기 = pd.read_parquet (파일 잠금 없음 → Streamlit·FastAPI·refresh.py 다중 프로세스 동시 OK).
- 필터 집계 = query(): 인메모리 DuckDB가 parquet를 직접 조회(잠금 없음) — FastAPI/즉시조회용.
- Databricks는 읽기 전용 소스. DuckDB 파일/단일 라이터 잠금 문제 없음(쓰기 권한도 불필요).
"""
from __future__ import annotations

import os
import json
import datetime as dt

import pandas as pd

import db

CACHE = os.environ.get(
    "WAREHOUSE_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache"),
)
SALES_WINDOW_DAYS = 45

_SNAPSHOTS = {
    "goods_master": db.load_goods_master,
    "inventory_pivot": db.load_inventory_pivot,
    "inventory_goods": db.load_inventory_goods,
    "inventory_store_long": db.load_inventory_store_long,
    "customer": db.load_customer,
    "target_daily": db.fetch_targets,   # 매장별 일 목표(gspread) — 목표 대비 실적 탭
    "footfall": db.fetch_footfall,             # 매장 입객수(일자×매장) — 구매전환율
    "global_customer": db.fetch_global_customer,  # 국가별 GMV(일자×매장×국적) — 고객 탭
    "settlement": db.fetch_settlement,         # 순이익(Net Take)·공헌이익(CP) 일자×매장×상품 — 판매 탭
    "settlement_option": db.fetch_settlement_option,  # 정산 상세 일자×매장×상품×옵션 — CSV 전용
    "settlement_daily": db.fetch_settlement_daily,    # 손익(P&L) 일자×매장×브랜드 — 손익 탭 전용
    "ips": db.fetch_ips,                                # 통합 IPS 브랜드×구분(매입/위탁) — IPS 탭 전용
    "ips_goods": db.fetch_ips_goods,                    # 통합 IPS 상품단위(드릴다운) — IPS 탭 상품 드릴
}
# readiness(=앱 구동 가능)에서 제외하는 선택 스냅샷: 아직 캐시에 없어도 앱은 정상 동작하고,
# 다음 full 갱신(mode=full/rebuild) 때 생성되면 자동으로 뷰가 잡힌다(receipts와 동일 취급).
_OPTIONAL = {"target_daily", "footfall", "global_customer", "settlement", "settlement_option", "settlement_daily", "ips", "ips_goods"}
_ALL = ["sales", *_SNAPSHOTS]
# DuckDB 뷰 생성 대상(=_ALL + 객단가용 영수증). receipts는 readiness(missing) 게이트에는 넣지 않아
# 기존 캐시만 있어도 앱이 동작하고, 판매 갱신/빌드 시 생성되면 자동으로 뷰가 잡힌다.
_VIEW_TABLES = [*_ALL, "receipts"]


def _path(name: str) -> str:
    return os.path.join(CACHE, f"{name}.parquet")


def _write(df: pd.DataFrame, name: str, row_group_size: int | None = None):
    os.makedirs(CACHE, exist_ok=True)
    tmp = _path(name) + f".tmp{os.getpid()}"
    # row_group_size 지정 시 여러 row group으로 분할 → 정렬된 컬럼(sales_date)의 min/max 존맵으로
    # DuckDB가 날짜 범위 밖 row group을 스킵(예측 프루닝)한다. None이면 pyarrow 기본값.
    df.to_parquet(tmp, index=False, row_group_size=row_group_size)
    os.replace(tmp, _path(name))   # 원자적 교체


def _meta_load() -> dict:
    p = os.path.join(CACHE, "_meta.json")
    try:
        os.stat(p)   # GCS FUSE: 명시적 stat로 메타 재검증 → 다른 인스턴스가 갱신한 최신 내용을 읽음(bare open은 stale 가능)
    except OSError:
        pass
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _meta_set(**kw):
    m = _meta_load()
    m.update(kw)
    os.makedirs(CACHE, exist_ok=True)
    # 원자적 교체(tmp→os.replace) — full 재빌드처럼 _meta_set이 연속 호출될 때, 다른 호출의
    # _meta_load가 '쓰는 도중(truncate)'을 읽어 {}로 오판→기존 키 유실되는 것을 방지.
    p = os.path.join(CACHE, "_meta.json")
    tmp = p + f".tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    os.replace(tmp, p)


KST = dt.timezone(dt.timedelta(hours=9))  # Cloud Run 컨테이너 TZ=UTC라도 표시는 한국시간(KST)으로


def now_kst_min() -> str:
    """현재 KST 시각 'YYYY-MM-DDTHH:MM' (오프셋 미포함 — 프론트가 문자열 슬라이스로 표시)."""
    return dt.datetime.now(KST).strftime("%Y-%m-%dT%H:%M")


def today_kst() -> str:
    """오늘 날짜(KST) 'YYYY-MM-DD' — 일별 스냅샷 1회 판정 기준."""
    return dt.datetime.now(KST).strftime("%Y-%m-%d")


def _stamp() -> str:
    return now_kst_min()


# ------------------------------------------------------- 판매 (증분 누적)
def _incremental(name: str, fetch_fn, window_days: int, full: bool):
    """판매계열 테이블(sales/receipts)의 증분 갱신: 최근 window만 재적재, 과거는 유지.
       sales_date 컬럼 기준. (full=True 또는 파일 없으면 전체 재조회)"""
    p = _path(name)
    if full or not os.path.exists(p):
        df = fetch_fn(None)
        mode = "full"
    else:
        cur = pd.read_parquet(p)
        cur["sales_date"] = pd.to_datetime(cur["sales_date"])
        wm = cur["sales_date"].max().date()
        since = wm - dt.timedelta(days=window_days)
        new = fetch_fn(since.isoformat())                # 최근 window만 재조회
        keep = cur[cur["sales_date"].dt.date < since]    # window 밖(과거)은 그대로 유지
        df = pd.concat([keep, new], ignore_index=True)   # window 교체 → 중복 불가
        mode = f"incremental(since {since})"
    # sales_date로 정렬 후 저장 → parquet row group이 날짜순 클러스터링되어(아래 row_group_size와 함께)
    # 날짜 범위 필터가 관련 row group만 스캔(존맵 프루닝). 거의 모든 조회가 기간 필터를 쓰므로 체감 큼.
    if "sales_date" in df.columns:
        df = df.sort_values("sales_date", kind="stable").reset_index(drop=True)
    _write(df, name, row_group_size=128_000)
    return df, mode


def refresh_sales(window_days: int = SALES_WINDOW_DAYS, full: bool = False) -> dict:
    df, mode = _incremental("sales", db.fetch_sales, window_days, full)
    rdf, _ = _incremental("receipts", db.fetch_receipts, window_days, full)  # 객단가용 영수증 집계
    mx = str(pd.to_datetime(df["sales_date"]).max().date())
    ts = db.sales_latest_ts()   # 가장 최근 거래 시각(원천 timestamp)
    _meta_set(sales_refreshed_at=_stamp(), sales_max_date=mx, sales_max_ts=ts,
              sales_rows=int(len(df)), receipts_rows=int(len(rdf)))
    return {"mode": mode, "rows": int(len(df)), "receipts": int(len(rdf)), "max_date": mx, "max_ts": ts}


# ------------------------------------------------------- 스냅샷 (전체 교체)
def refresh_snapshot(name: str) -> int:
    df = _SNAPSHOTS[name]()
    _write(df, name)
    kw = {f"{name}_refreshed_at": _stamp()}
    if "data_date" in getattr(df, "attrs", {}):   # 스냅샷 자체의 기준일(예: 재고 ord_state_date)
        kw[f"{name}_data_date"] = df.attrs["data_date"]
    _meta_set(**kw)
    return len(df)


# 야간 full(refresh_all)에서 제외하는 무거운 스냅샷 — 별도 스케줄러 잡이 mode=snap 으로 갱신.
# (IPS는 웜 ~10분이라 동기 full HTTP의 Cloud Run 요청 타임아웃을 압박 → core 리프레시와 분리)
_DECOUPLED = {"ips", "ips_goods"}


def refresh_snapshots() -> dict:
    # 스냅샷별 소요시간을 stdout에 남겨 어느 것이 느린지 로그로 드러나게(리프레시 병목 진단).
    import time as _t
    out = {}
    for n in _SNAPSHOTS:
        if n in _DECOUPLED:
            continue
        t0 = _t.time()
        rows = refresh_snapshot(n)
        print(f"    [snap] {n}: {rows} rows in {_t.time() - t0:.1f}s", flush=True)
        out[n] = rows
    return out


def refresh_named(names: list[str]) -> dict:
    """지정 스냅샷만 교체(전체 full 없이). 무거운 신규 스냅샷(예: ips)을 야간 full과
       분리해 독립적으로 채우거나 갱신할 때 사용. 알 수 없는 이름은 무시."""
    return {n: refresh_snapshot(n) for n in names if n in _SNAPSHOTS}


def refresh_all(full: bool = False) -> dict:
    out = {"sales": refresh_sales(full=full)}
    out.update(refresh_snapshots())
    return out


# ------------------------------------------------------- 상태
def missing() -> list[str]:
    return [n for n in _ALL if n not in _OPTIONAL and not os.path.exists(_path(n))]


def is_ready() -> bool:
    return not missing()


def _mtime_kst(name: str) -> str | None:
    """캐시 parquet 파일의 실제 수정시각(KST 'YYYY-MM-DDTHH:MM'). = 실제 갱신 시각."""
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(_path(name)), KST).strftime("%Y-%m-%dT%H:%M")
    except OSError:
        return None


def status() -> dict:
    m = _meta_load()
    m["missing"] = missing()
    # 신선도 라벨을 실제 캐시 파일에서 직접 도출 → _meta.json이 마운트 캐시로 stale해도 항상 정확.
    # (갱신시각 = parquet 파일 mtime, 판매 데이터일 = 캐시 라이브 MAX)
    for nm, key in (("sales", "sales_refreshed_at"),
                    ("inventory_pivot", "inventory_pivot_refreshed_at"),
                    ("customer", "customer_refreshed_at"),
                    ("goods_master", "goods_master_refreshed_at")):
        t = _mtime_kst(nm)
        if t:
            m[key] = t
    try:
        d = query("SELECT CAST(max(sales_date) AS DATE) d FROM sales").iloc[0]["d"]
        if d is not None:
            m["sales_max_date"] = str(d)[:10]   # 'YYYY-MM-DD' (Timestamp의 시각부 제거)
    except Exception:
        pass
    return m


# ------------------------------------------------------- 읽기 (pandas, mtime 캐시)
# parquet를 매 요청 재파싱하지 않도록 프로세스 메모리에 캐시(파일 mtime 바뀌면 자동 갱신).
# 반환 df는 공유본 → 호출부는 변형 전 .copy()/필터로 새 프레임 생성할 것.
import threading

_pcache: dict = {}        # name -> (mtime, df)
_pcache_lock = threading.Lock()


def _read_cached(name: str, post=None) -> pd.DataFrame:
    p = _path(name)
    m = os.path.getmtime(p)
    with _pcache_lock:
        hit = _pcache.get(name)
        if hit and hit[0] == m:
            return hit[1]
    df = pd.read_parquet(p)
    if post:
        df = post(df)
    with _pcache_lock:
        _pcache[name] = (m, df)
    return df


def _post_sales(df):
    df["sales_date"] = pd.to_datetime(df["sales_date"])
    df["goods_no"] = pd.to_numeric(df["goods_no"]).astype("int64")
    if "reg_date" in df.columns:
        df["reg_date"] = pd.to_datetime(df["reg_date"], errors="coerce")
    return df


def _post_goods(df):
    df["goods_no"] = pd.to_numeric(df["goods_no"]).astype("int64")
    if "reg_date" in df.columns:
        df["reg_date"] = pd.to_datetime(df["reg_date"], errors="coerce")
    return df


def get_sales() -> pd.DataFrame:
    return _read_cached("sales", _post_sales)


def get_goods_master() -> pd.DataFrame:
    return _read_cached("goods_master", _post_goods)


def get_customer() -> pd.DataFrame:
    return _read_cached("customer", lambda df: df.assign(sales_date=pd.to_datetime(df["sales_date"])))


def get_inventory_pivot() -> pd.DataFrame:
    return _read_cached("inventory_pivot")


def get_inventory_goods() -> pd.DataFrame:
    return _read_cached("inventory_goods", lambda df: df.assign(goods_no=pd.to_numeric(df["goods_no"]).astype("int64")))


def get_inventory_store_long() -> pd.DataFrame:
    return _read_cached("inventory_store_long", lambda df: df.assign(goods_no=pd.to_numeric(df["goods_no"]).astype("int64")))


# ------------------------------------------------------- DuckDB 필터 쿼리 (영구 연결 + parquet 뷰/테이블)
# 연결/뷰 생성은 1회만(프로세스 영속) → 매 호출 연결비용 제거. 뷰는 read_parquet라 데이터를
# 메모리에 적재하지 않음(저메모리) + 쿼리 시 필요한 컬럼/행만 GCS에서 읽어(프로젝션·존맵 프루닝)
# '최초 로딩'이 가볍다. mtime 바뀐 것만 CREATE OR REPLACE → 갱신 자동 반영. 커서로 동시 읽기 안전.
#
# 네이티브 인메모리 테이블(옵션): 지정한 테이블을 통째로 메모리에 적재 → 웜 상태 쿼리 약 3~4x 빠름.
#   단 첫 쿼리 전에 전체를 선(先)적재하므로 scale-to-zero 콜드스타트 시 '최초 로딩'이 크게 느려진다
#   (GCS에서 sales+receipts 수백 MB를 통째로 읽음, 상주 +~1.2GiB). → 기본 비활성(뷰).
#   상시가동(min=1)으로 콜드스타트가 없어지면 그때 env로 켜서 웜 이점만 취할 것:
#     DUCKDB_NATIVE="sales,receipts"
_NATIVE = {t.strip() for t in os.environ.get("DUCKDB_NATIVE", "").split(",") if t.strip()}
_DB = None
_loaded: dict = {}        # name -> mtime
_db_lock = threading.Lock()


def _ensure_db():
    import duckdb
    global _DB
    with _db_lock:
        if _DB is None:
            _DB = duckdb.connect()
        for name in _VIEW_TABLES:
            p = _path(name)
            if not os.path.exists(p):
                continue
            m = os.path.getmtime(p)
            if _loaded.get(name) != m:
                src = f"SELECT * FROM read_parquet('{p.replace(chr(92), '/')}')"
                kind = "TABLE" if name in _NATIVE else "VIEW"
                _DB.execute(f"CREATE OR REPLACE {kind} {name} AS {src}")
                _loaded[name] = m
    return _DB


def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """영구 연결의 parquet 뷰(sales/goods_master/inventory_*/customer)에 SQL 실행.
    매 호출 연결/뷰생성 비용 없음. 커서로 동시 읽기 안전. mtime 기반 뷰 자동 재생성."""
    db = _ensure_db()
    cur = db.cursor()
    try:
        return cur.execute(sql, params or []).df()
    finally:
        cur.close()
