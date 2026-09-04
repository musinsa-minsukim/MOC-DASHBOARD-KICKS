"""무신사 오프라인 대시보드 — FastAPI 백엔드.

Parquet 저장소(store)를 DuckDB로 즉시 조회해 필터→JSON 반환. React 프론트가 호출.
- 로그인: 기존 auth_config.yaml 계정(bcrypt) 재사용 → 서명 토큰(HMAC) 발급.
- 보호 엔드포인트는 Authorization: Bearer <token> 필요. (API_NO_AUTH=1이면 우회: 개발/테스트용)
- Parquet 읽기라 Streamlit/refresh와 동시 실행 안전.

실행:  uv run uvicorn api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import time
import hmac
import math
import base64
import hashlib
import logging
import threading

import yaml
import bcrypt
from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import store
import daily
import invtab
import cmptab
import prodmeta
import drill

import mimetypes
mimetypes.add_type("application/manifest+json", ".webmanifest")  # PWA manifest 정상 서빙

APP_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="무신사 오프라인 대시보드 API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ----------------------------------------------------------------- 인증
def _auth_cfg() -> dict:
    # 배포 시 시크릿은 /app 밖(예: /secrets/auth_config.yaml)에 마운트 — /app 디렉터리 shadowing 방지.
    path = os.environ.get("AUTH_CONFIG") or os.path.join(APP_DIR, "auth_config.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _cookie_key() -> str:
    # 배포: COOKIE_KEY 환경변수 우선(시크릿 주입). 로컬: auth_config.yaml.
    key = os.environ.get("COOKIE_KEY") or (_auth_cfg().get("cookie") or {}).get("key")
    if not key or key == "change-me":
        raise RuntimeError("COOKIE_KEY(또는 auth_config.yaml cookie.key)가 설정되어야 합니다 (토큰 서명 시크릿). 기본값 폴백 금지.")
    return key


def _users() -> dict:
    return (_auth_cfg().get("credentials") or {}).get("usernames") or {}


def make_token(username: str, days: int = 30) -> str:
    exp = int(time.time()) + days * 86400
    msg = f"{username}|{exp}"
    sig = hmac.new(_cookie_key().encode(), msg.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{msg}|{sig}".encode()).decode()


def verify_token(token: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        username, exp, sig = raw.rsplit("|", 2)
        if int(exp) < time.time():
            return None
        good = hmac.new(_cookie_key().encode(), f"{username}|{exp}".encode(), hashlib.sha256).hexdigest()
        return username if hmac.compare_digest(good, sig) else None
    except Exception:
        return None


def require_user(authorization: str = Header(default="")) -> str:
    # 인증 우회는 명시적 비프로덕션(APP_ENV)에서만 허용 — 프로덕션에선 플래그를 무시하고 항상 토큰 요구.
    if os.environ.get("API_NO_AUTH") == "1" and os.environ.get("APP_ENV", "").lower() in {"dev", "test", "local"}:
        logging.warning("AUTH BYPASS active (API_NO_AUTH=1, APP_ENV non-prod)")
        return "dev"
    user = verify_token(authorization.replace("Bearer ", "").strip())
    if not user:
        raise HTTPException(status_code=401, detail="unauthorized")
    return user


def require_ready() -> None:
    if not store.is_ready():
        raise HTTPException(status_code=503, detail={"error": "데이터가 아직 준비되지 않았습니다", "missing": store.missing()})


class LoginBody(BaseModel):
    username: str
    password: str


# ----------------------------------------------------------------- 필터 → WHERE
_IN_COLS = {
    "biz": "business_type", "type": "shop_type", "store": "store_name", "brand": "brand_nm",
    "cat_top": "cat_top", "cat_large": "cat_large", "cat_medium": "cat_medium", "md": "off_md_id",
    "concept": "concept",
}


def get_filters(
    date_from: str | None = None, date_to: str | None = None,
    biz: list[str] = Query(default=[]), type: list[str] = Query(default=[]),
    store: list[str] = Query(default=[]), brand: list[str] = Query(default=[]),
    cat_top: list[str] = Query(default=[]), cat_large: list[str] = Query(default=[]),
    cat_medium: list[str] = Query(default=[]), md: list[str] = Query(default=[]),
    goods: list[int] = Query(default=[]), name_like: str | None = None,
    brand_ex: list[str] = Query(default=[]), running: int | None = None,
    concept: list[str] = Query(default=[]),
) -> dict:
    return {"date_from": date_from, "date_to": date_to, "biz": biz, "type": type,
            "store": store, "brand": brand, "cat_top": cat_top, "cat_large": cat_large,
            "cat_medium": cat_medium, "md": md, "goods": goods, "name_like": name_like,
            "brand_ex": brand_ex, "running": running, "concept": concept}


def build_where(f: dict):
    clauses, params = [], []
    # 날짜 필터는 정렬·존맵 기준 컬럼(sales_date)에 직접 비교 → parquet row-group 프루닝 최대화.
    # (CAST(sales_date AS DATE)로 감싸면 프루닝이 약해져 느림. sales_date는 자정 고정이라 결과 동일.)
    if f.get("date_from"):
        clauses.append("sales_date >= CAST(? AS DATE)"); params.append(f["date_from"])
    if f.get("date_to"):
        clauses.append("sales_date < CAST(? AS DATE) + INTERVAL 1 DAY"); params.append(f["date_to"])
    for key, col in _IN_COLS.items():
        vals = f.get(key)
        if vals:
            clauses.append(f"{col} IN ({','.join(['?'] * len(vals))})")
            params += list(vals)
    if f.get("goods"):
        g = [int(x) for x in f["goods"]]
        clauses.append(f"goods_no IN ({','.join(['?'] * len(g))})")
        params += g
    if f.get("name_like"):
        # 상품명 부분일치(대소문자 무시). 예: ACG. sales에 goods_nm 존재.
        clauses.append("lower(goods_nm) LIKE ?")
        params.append("%" + str(f["name_like"]).lower() + "%")
    if f.get("brand_ex"):   # 브랜드 제외
        clauses.append(f"brand_nm NOT IN ({','.join(['?'] * len(f['brand_ex']))})")
        params += list(f["brand_ex"])
    if f.get("running"):    # 러닝화만 (RUN 매장 취급 신발) — sales에 is_running 존재
        clauses.append("is_running = 1")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


# 객단가(영수증) 그레인이 지원하는 IN 필터: 사업구분·매장타입·매장·브랜드·카테(3단). (md·goods는 영수증에 없음)
_RCPT_IN_COLS = {k: _IN_COLS[k] for k in ("biz", "type", "store", "brand", "cat_top", "cat_large", "cat_medium")}


def build_where_receipts(f: dict):
    """객단가(영수증) 전용 WHERE — 기간·매장·매장타입·사업구분·브랜드·카테(최상위/대/중) 적용.
       (md·goods는 영수증 그레인에 없어 미적용.) 영수증은 order_id 단위라 _aov에서 COUNT(DISTINCT)로 집계."""
    clauses, params = [], []
    if f.get("date_from"):
        clauses.append("sales_date >= CAST(? AS DATE)"); params.append(f["date_from"])
    if f.get("date_to"):
        clauses.append("sales_date < CAST(? AS DATE) + INTERVAL 1 DAY"); params.append(f["date_to"])
    for key, col in _RCPT_IN_COLS.items():
        vals = f.get(key)
        if vals:
            clauses.append(f"{col} IN ({','.join(['?'] * len(vals))})")
            params += list(vals)
    if f.get("brand_ex"):
        clauses.append(f"brand_nm NOT IN ({','.join(['?'] * len(f['brand_ex']))})")
        params += list(f["brand_ex"])
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


# settlement(순이익/CP) 뷰가 지원하는 IN 필터: 매장타입·매장만(브랜드·카테·사업구분·md는 뷰에 없음 → goods_no 조인으로 간접 반영).
_STL_IN_COLS = {"type": "shop_type", "store": "store_name"}


def build_where_settlement(f: dict):
    """settlement(순이익·CP) 전용 WHERE — 기간·매장타입·매장만. (브랜드/카테/사업구분/md는 뷰에 없어
       goods_no 조인으로 반영.) settlement 그레인 = 일자×매장×상품."""
    clauses, params = [], []
    if f.get("date_from"):
        clauses.append("sales_date >= CAST(? AS DATE)"); params.append(f["date_from"])
    if f.get("date_to"):
        clauses.append("sales_date < CAST(? AS DATE) + INTERVAL 1 DAY"); params.append(f["date_to"])
    for key, col in _STL_IN_COLS.items():
        vals = f.get(key)
        if vals:
            clauses.append(f"{col} IN ({','.join(['?'] * len(vals))})")
            params += list(vals)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


# 오늘자(원천 ~1일 지연으로 settlement 미반영일) 순이익 추정: settlement 없는 (일자×goods)는
# 상품 take rate(net_take/gmv) × 그날 GMV로 보정(잠정). ⭐ rate는 '필터 구간'이 아니라 '최근 45일'에서
# 산출 → '오늘 하루'만 조회해도 과거 rate로 추정 가능(구간 내 실적 0이어도 OK). CP는 추정 안 함(실적만).
# 최근 45일에도 정산 커버가 없는 goods(gr.cov=0/없음)는 net_take=NULL(공란) 유지 — 오추정 방지.
_STL_CTE = """
    g AS (SELECT DISTINCT goods_no FROM sales{ws}),
    rs AS (SELECT sales_date, goods_no, SUM(gmv) gmv FROM sales
           WHERE sales_date >= current_date - INTERVAL 45 DAY AND goods_no IN (SELECT goods_no FROM g) GROUP BY 1, 2),
    rst AS (SELECT sales_date, goods_no, SUM(net_take) net_take FROM settlement
            WHERE sales_date >= current_date - INTERVAL 45 DAY GROUP BY 1, 2),
    rj AS (SELECT rs.goods_no, rs.gmv, rst.net_take FROM rs LEFT JOIN rst ON rst.sales_date = rs.sales_date AND rst.goods_no = rs.goods_no),
    gr AS (SELECT goods_no, SUM(net_take) / NULLIF(SUM(CASE WHEN net_take IS NOT NULL THEN gmv END), 0) rate_g,
                  SUM(CASE WHEN net_take IS NOT NULL THEN 1 ELSE 0 END) cov FROM rj GROUP BY goods_no),
    ov AS (SELECT SUM(net_take) / NULLIF(SUM(CASE WHEN net_take IS NOT NULL THEN gmv END), 0) r FROM rj),
    s AS (SELECT sales_date, goods_no, SUM(gmv) gmv FROM sales{ws} GROUP BY 1, 2),
    st AS (SELECT sales_date, goods_no, SUM(net_take) net_take, SUM(cp) cp FROM settlement{wstl} GROUP BY 1, 2),
    j AS (SELECT s.goods_no, s.gmv, st.net_take, st.cp
          FROM s LEFT JOIN st ON st.sales_date = s.sales_date AND st.goods_no = s.goods_no)"""
_STL_NT = "COALESCE(j.net_take, COALESCE(gr.rate_g, ov.r) * j.gmv)"   # 커버분=실적, 미커버(오늘자)=최근 rate 추정


def _stl_none(x):
    return None if (x is None or x != x) else _num(x)   # NaN/NULL → None(공란), 아니면 숫자


def _settlement_by_goods(f: dict) -> dict:
    """goods별 순이익(net_take, 오늘자 추정 포함)·CP(실적). {goods_no: (net_take, cp)}.
       settlement 커버 없는 goods → (None, None), 캐시 없으면 {}."""
    try:
        wsales, psales = build_where(f)
        wstl, pstl = build_where_settlement(f)
        df = store.query(f"""
            WITH {_STL_CTE.format(ws=wsales, wstl=wstl)}
            SELECT j.goods_no,
                   CASE WHEN MAX(gr.cov) > 0 THEN CAST(SUM({_STL_NT}) AS DOUBLE) END net_take,
                   CASE WHEN MAX(gr.cov) > 0 THEN CAST(SUM(j.cp) AS DOUBLE) END cp
            FROM j LEFT JOIN gr ON gr.goods_no = j.goods_no CROSS JOIN ov
            GROUP BY j.goods_no""", psales + psales + pstl)
    except Exception:
        return {}
    return {int(r.goods_no): (_stl_none(r.net_take), _stl_none(r.cp)) for r in df.itertuples()}


def _settlement_totals(f: dict):
    """필터 정합 순이익(오늘자 추정 포함)·CP(실적) 합. settlement 캐시 없거나 오류면 None."""
    try:
        wsales, psales = build_where(f)
        wstl, pstl = build_where_settlement(f)
        r = store.query(f"""
            WITH {_STL_CTE.format(ws=wsales, wstl=wstl)}
            SELECT CAST(SUM(CASE WHEN gr.cov > 0 THEN {_STL_NT} END) AS DOUBLE) nt,
                   CAST(SUM(CASE WHEN gr.cov > 0 THEN j.cp END) AS DOUBLE) cp
            FROM j LEFT JOIN gr ON gr.goods_no = j.goods_no CROSS JOIN ov""", psales + psales + pstl).iloc[0]
    except Exception:
        return None
    return _num(r.nt), _num(r.cp)


def _settlement_by_brand(f: dict) -> dict:
    """(사업구분, 브랜드)별 순이익(오늘자 추정 포함)·CP(실적). sales의 goods→브랜드 매핑으로 집계."""
    try:
        wsales, psales = build_where(f)
        wstl, pstl = build_where_settlement(f)
        df = store.query(f"""
            WITH {_STL_CTE.format(ws=wsales, wstl=wstl)},
                 gb AS (SELECT DISTINCT goods_no, business_type, brand_nm FROM sales{wsales})
            SELECT gb.business_type, gb.brand_nm,
                   CAST(SUM(CASE WHEN gr.cov > 0 THEN {_STL_NT} END) AS DOUBLE) net_take,
                   CAST(SUM(CASE WHEN gr.cov > 0 THEN j.cp END) AS DOUBLE) cp
            FROM j LEFT JOIN gr ON gr.goods_no = j.goods_no CROSS JOIN ov
                 JOIN gb ON gb.goods_no = j.goods_no
            GROUP BY 1, 2""", psales + psales + pstl + psales)
    except Exception:
        return {}
    return {(r.business_type, r.brand_nm): (_stl_none(r.net_take), _stl_none(r.cp)) for r in df.itertuples()}


def _aov(f: dict):
    """receipts 뷰에서 객단가 계산(전체/내국인/외국인). receipts 캐시가 아직 없으면 None."""
    try:
        where, params = build_where_receipts(f)
        r = store.query(f"""
            SELECT CAST(COUNT(DISTINCT order_id) AS DOUBLE) r, CAST(sum(gmv) AS DOUBLE) g,
                   CAST(COUNT(DISTINCT CASE WHEN is_foreign=1 THEN order_id END) AS DOUBLE) fr,
                   CAST(sum(CASE WHEN is_foreign=1 THEN gmv ELSE 0 END) AS DOUBLE) fg
            FROM receipts{where}""", params).iloc[0]
    except Exception:
        return None
    tr, tg = _num(r.r), _num(r.g)
    fr, fg = _num(r.fr), _num(r.fg)
    dr, dg = tr - fr, tg - fg
    mk = lambda rc, g: {"receipts": int(rc), "gmv": g, "aov": (g / rc) if rc else 0}
    return {**mk(tr, tg), "domestic": mk(dr, dg), "foreign": mk(fr, fg)}


# ----------------------------------------------------------------- 엔드포인트
@app.get("/api/health")
def health():
    return {"status": "ok", "ready": store.is_ready()}


# ----------------------------------------------------------------- 데이터 갱신 (판매 증분)
_refresh = {"running": False, "error": None, "finished_at": None, "result": None}


@app.get("/api/status")
def status_detail(_: str = Depends(require_user)):
    s = store.status()
    s["refreshing"] = _refresh["running"]
    s["refresh_error"] = _refresh["error"]
    s["refresh_finished_at"] = _refresh["finished_at"]
    return s


def _run_sales_refresh():
    _refresh.update(running=True, error=None)
    try:
        _refresh["result"] = store.refresh_sales()        # MOSS 증분 재pull(최근 N일) + 누적 교체
    except Exception as e:
        _refresh["error"] = str(e)
        logging.exception("sales refresh failed")
    finally:
        _refresh["running"] = False
        _refresh["finished_at"] = store.now_kst_min()


@app.post("/api/refresh/sales")
def refresh_sales_ep(_: str = Depends(require_user), __: None = Depends(require_ready)):
    """판매 데이터 증분 갱신을 백엔드에서 실행(백그라운드 스레드, 동시 1회). 진행상황은 /api/status."""
    if _refresh["running"]:
        return {"started": False, "running": True}
    threading.Thread(target=_run_sales_refresh, daemon=True).start()
    return {"started": True, "running": True}


# ----------------------------------------------------------------- Cron 갱신 (Cloud Scheduler)
def require_cron(x_cron_token: str = Header(default="")) -> None:
    """Cloud Scheduler 전용 보호. 서비스가 공개(allUsers)라 Cloud Run IAM으로 cron만 게이트할 수
       없으므로, 공유 시크릿 토큰을 앱 레벨에서 검증. CRON_TOKEN 미설정이면 비활성(fail-closed)."""
    expected = os.environ.get("CRON_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="cron disabled (CRON_TOKEN unset)")
    if not hmac.compare_digest(x_cron_token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.post("/api/cron/refresh")
def cron_refresh(mode: str = Query("sales"), names: str = Query(""), _: None = Depends(require_cron)):
    """Cloud Scheduler가 호출하는 '동기' 갱신 — scale-to-zero 환경에서 요청이 끝날 때까지
       인스턴스가 살아있어 갱신이 끝까지 실행된다(백그라운드 스레드면 응답 후 CPU 회수로 중단될 수 있음).
         mode=full    → 전체(판매 증분 + 스냅샷 전체 교체). 매일 07:00.  캐시 비었으면 전체 빌드도 겸함.
         mode=sales   → 판매 증분(+영수증). 매시 10분(11:10~23:10).
         mode=rebuild → sales·receipts까지 전체 재빌드(full). 스키마/집계 로직 변경 후 1회 수동 호출용.
         mode=snap&names=a,b → 지정 스냅샷만 교체(무거운 신규 캐시를 야간 full과 분리해 독립 갱신).
       store._write는 원자적(tmp→os.replace)이라 갱신 중에도 읽기(사용자 조회)는 안전."""
    if _refresh["running"]:
        return {"ok": False, "running": True, "skipped": "already running"}
    _refresh.update(running=True, error=None)
    t0 = time.time()
    try:
        if mode == "snap":
            # 지정 스냅샷만 교체(전체 full 없이). 예: ?mode=snap&names=ips,ips_goods
            wanted = [s.strip() for s in names.split(",") if s.strip()]
            result = store.refresh_named(wanted)
        elif mode == "rebuild":
            result = store.refresh_all(full=True)     # sales·receipts 전체 재빌드(스키마/로직 변경 후 1회)
        elif mode == "full":
            result = store.refresh_all()
        else:
            result = store.refresh_sales()
        _refresh["result"] = result
        return {"ok": True, "mode": mode, "elapsed_sec": round(time.time() - t0, 1), "result": result}
    except Exception as e:
        _refresh["error"] = str(e)
        logging.exception("cron refresh failed (mode=%s)", mode)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _refresh["running"] = False
        _refresh["finished_at"] = store.now_kst_min()


# 자동 갱신 스케줄러 — 배포 환경에서 PC 없이 상시 최신 유지 (env AUTO_REFRESH_MINUTES).
# 매 주기 판매 증분, 하루 한 번 스냅샷(재고/상품/고객)까지 갱신.
def _auto_refresh_loop(minutes: int):
    while True:
        time.sleep(minutes * 60)
        if _refresh["running"]:
            continue
        try:
            today = store.today_kst()
            need_snap = (store.status().get("inventory_pivot_refreshed_at") or "")[:10] != today
            _refresh.update(running=True, error=None)
            try:
                if need_snap:
                    store.refresh_all()          # 판매 + 스냅샷 전체(하루 1회)
                else:
                    store.refresh_sales()         # 판매 증분
            finally:
                _refresh.update(running=False, finished_at=store.now_kst_min())
        except Exception as e:
            _refresh["error"] = str(e)
            logging.exception("auto-refresh loop error")


def _run_full_build():
    """캐시가 비어있을 때(최초 배포) 전체 빌드(판매+스냅샷)."""
    if _refresh["running"]:
        return
    _refresh.update(running=True, error=None)
    try:
        store.refresh_all()
    except Exception as e:
        _refresh["error"] = str(e)
        logging.exception("initial full build failed")
    finally:
        _refresh.update(running=False, finished_at=store.now_kst_min())


@app.on_event("startup")
def _start_auto_refresh():
    if store.missing():   # 최초 배포: 캐시 비어있으면 백그라운드 전체 빌드
        threading.Thread(target=_run_full_build, daemon=True).start()
        logging.warning("cache empty → initial full build started")
    m = os.environ.get("AUTO_REFRESH_MINUTES", "")
    if m.isdigit() and int(m) > 0:
        threading.Thread(target=_auto_refresh_loop, args=(int(m),), daemon=True).start()
        logging.warning("auto-refresh enabled: every %s min (snapshots daily)", m)


@app.post("/api/login")
def login(body: LoginBody):
    u = _users().get(body.username)
    if not u or not bcrypt.checkpw(body.password.encode(), str(u["password"]).encode()):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    return {"token": make_token(body.username), "name": u.get("name", body.username)}


@app.get("/api/meta")
def meta(_: str = Depends(require_user), __: None = Depends(require_ready)):
    rng = store.query("SELECT CAST(min(sales_date) AS DATE) lo, CAST(max(sales_date) AS DATE) hi FROM sales")
    stores = store.query("SELECT DISTINCT store_name, shop_type FROM sales ORDER BY shop_type, store_name")
    brands = store.query("SELECT brand_nm, CAST(sum(gmv) AS DOUBLE) gmv FROM sales GROUP BY 1 ORDER BY gmv DESC")
    biz = store.query("SELECT DISTINCT business_type FROM sales WHERE business_type IS NOT NULL ORDER BY 1")
    cats = store.query("""SELECT 'cat_top' k, cat_top v FROM sales WHERE cat_top IS NOT NULL GROUP BY 1,2
                          UNION ALL SELECT 'cat_large', cat_large FROM sales WHERE cat_large IS NOT NULL GROUP BY 1,2
                          UNION ALL SELECT 'cat_medium', cat_medium FROM sales WHERE cat_medium IS NOT NULL GROUP BY 1,2""")
    md = store.query("SELECT DISTINCT off_md_id FROM sales WHERE off_md_id <> '' ORDER BY 1")
    try:
        concepts = store.query("SELECT DISTINCT concept FROM sales WHERE concept IS NOT NULL AND concept <> '' ORDER BY 1")["concept"].tolist()
    except Exception:
        concepts = []   # 캐시에 concept 열이 아직 없으면(재적재 전) 빈 목록 → 필터 UI 미표시
    return {
        "date_min": str(rng["lo"].iloc[0]), "date_max": str(rng["hi"].iloc[0]),
        "stores": stores.to_dict(orient="records"),
        "brands": brands["brand_nm"].tolist(),
        "shop_types": sorted(stores["shop_type"].unique().tolist()),
        "business_types": biz["business_type"].tolist(),
        "cat_top": sorted(cats[cats.k == "cat_top"]["v"].tolist()),
        "cat_large": sorted(cats[cats.k == "cat_large"]["v"].tolist()),
        "cat_medium": sorted(cats[cats.k == "cat_medium"]["v"].tolist()),
        "md": md["off_md_id"].tolist(),
        "concepts": concepts,
    }


def _num(v) -> float:
    """None/NaN/Inf → 0.0 (NaN은 invalid JSON이라 직렬화 전에 제거)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(x) or math.isinf(x) else x


@app.get("/api/summary")
def summary(f: dict = Depends(get_filters), _: str = Depends(require_user), __: None = Depends(require_ready)):
    where, params = build_where(f)
    df = store.query(f"""
        SELECT CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(qty) AS DOUBLE) qty,
               CAST(sum(normal_amt) AS DOUBLE) normal_amt, CAST(sum(pay) AS DOUBLE) pay,
               CAST(sum(foreign_gmv) AS DOUBLE) foreign_gmv,
               count(DISTINCT goods_no) goods_count, count(DISTINCT store_name) store_count
        FROM sales{where}""", params)
    r = df.iloc[0]
    gmv = _num(r.gmv); normal = _num(r.normal_amt); fgn = _num(r.foreign_gmv)
    tot = _settlement_totals(f)   # (net_take, cp) or None(캐시 미존재)
    out = {
        "gmv": gmv, "qty": _num(r.qty), "normal_amt": normal, "pay": _num(r.pay),
        "foreign_gmv": fgn, "goods_count": int(_num(r.goods_count)), "store_count": int(_num(r.store_count)),
        "discount_rate": (1 - gmv / normal) * 100 if normal else 0,
        "foreign_ratio": (fgn / gmv * 100) if gmv else 0,
    }
    if tot is not None:
        nt, cp = tot
        out["net_take"] = nt
        out["cp"] = cp
        # 공헌이익률 = CP / (GMV/1.1) (부가세 제외 거래액 대비). GMV는 대시보드(MOSS) 기준.
        out["cp_rate"] = (cp / (gmv / 1.1) * 100) if gmv else 0
    return out


@app.get("/api/aov")
def aov(f: dict = Depends(get_filters), _: str = Depends(require_user), __: None = Depends(require_ready)):
    """객단가 = 판매가(order_amount) 합 ÷ 영수증수(distinct order_id). 기간·매장·매장타입·내외국인만 반영."""
    res = _aov(f)
    if res is None:
        z = {"receipts": 0, "gmv": 0, "aov": 0}
        res = {**z, "domestic": dict(z), "foreign": dict(z)}
    return res


@app.get("/api/hourly")
def hourly(f: dict = Depends(get_filters), _: str = Depends(require_user), __: None = Depends(require_ready)):
    """시간대별 매출(완료주문 거래시각 기준, 10~23시). receipts 뷰 사용 →
       기간·매장·매장타입·사업구분·브랜드·카테(3단) 필터 반영(build_where_receipts). 10~23시 전 구간 반환(빈 시각은 0)."""
    try:
        where, params = build_where_receipts(f)
        w = where + (" AND " if where else " WHERE ") + "hour BETWEEN 10 AND 23"
        r = store.query(f"""
            SELECT hour, CAST(sum(gmv) AS DOUBLE) gmv,
                   CAST(sum(CASE WHEN is_foreign=1 THEN gmv ELSE 0 END) AS DOUBLE) foreign_gmv,
                   CAST(COUNT(DISTINCT order_id) AS DOUBLE) receipts
            FROM receipts{w} GROUP BY hour ORDER BY hour""", params)
        by = {int(row.hour): row for row in r.itertuples()}
    except Exception:
        by = {}
    return [{"hour": h,
             "gmv": _num(by[h].gmv) if h in by else 0.0,
             "foreign_gmv": _num(by[h].foreign_gmv) if h in by else 0.0,
             "receipts": int(_num(by[h].receipts)) if h in by else 0} for h in range(10, 24)]


@app.get("/api/trend")
def trend(f: dict = Depends(get_filters), gran: str = "day", split: str | None = None,
          _: str = Depends(require_user), __: None = Depends(require_ready)):
    bucket = {"day": "CAST(sales_date AS DATE)",
              "week": "CAST(date_trunc('week', sales_date) AS DATE)",
              "month": "CAST(date_trunc('month', sales_date) AS DATE)"}.get(gran, "CAST(sales_date AS DATE)")
    where, params = build_where(f)
    if split == "business":   # 위탁/매입 적층용 (bucket × business_type)
        df = store.query(f"""
            SELECT {bucket} AS bucket, business_type, CAST(sum(gmv) AS DOUBLE) gmv
            FROM sales{where} GROUP BY 1, 2 ORDER BY 1""", params)
        df["bucket"] = df["bucket"].astype(str)
        return df.fillna(0).to_dict(orient="records")
    df = store.query(f"""
        SELECT {bucket} AS bucket, CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(qty) AS DOUBLE) qty,
               CAST(sum(foreign_gmv) AS DOUBLE) foreign_gmv
        FROM sales{where} GROUP BY 1 ORDER BY 1""", params)
    df["bucket"] = df["bucket"].astype(str)
    return df.fillna(0).to_dict(orient="records")


@app.get("/api/by/{dim}")
def by_dim(dim: str, f: dict = Depends(get_filters), limit: int = 100,
           _: str = Depends(require_user), __: None = Depends(require_ready)):
    col = {"store": "store_name", "brand": "brand_nm", "cat_top": "cat_top",
           "cat_large": "cat_large", "cat_medium": "cat_medium", "business": "business_type",
           "concept": "concept"}.get(dim)
    if not col:
        raise HTTPException(status_code=400, detail=f"unknown dim: {dim}")
    where, params = build_where(f)
    df = store.query(f"""
        SELECT {col} AS name, CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(qty) AS DOUBLE) qty,
               CAST(sum(foreign_gmv) AS DOUBLE) foreign_gmv, count(DISTINCT goods_no) goods_count,
               CAST(sum(sum(gmv)) OVER () AS DOUBLE) grand_total
        FROM sales{where} GROUP BY 1 ORDER BY gmv DESC LIMIT ?""", params + [int(limit)])
    return df.fillna(0).to_dict(orient="records")


@app.get("/api/drill")
def drill_ep(level: str = "shop", f: dict = Depends(get_filters), limit: int = 500,
             _: str = Depends(require_user), __: None = Depends(require_ready)):
    """드릴다운 — 매장→브랜드→대카테→중카테→상품. level별 판매지표+점재고, 상위 필터는 f로 캐스케이드.
       레벨: shop|brand|cat_large|cat_medium|goods."""
    if level not in drill.LEVEL_COL:
        raise HTTPException(status_code=400, detail=f"unknown level: {level}")
    return drill.rows(level, f, int(limit))


@app.get("/api/drill.csv")
def drill_csv_ep(level: str = "shop", f: dict = Depends(get_filters),
                 _: str = Depends(require_user), __: None = Depends(require_ready)):
    """드릴 결과를 tidy/long CSV로 — 엔티티당 1행(열=차원+지표), 상위 단일필터는 컨텍스트 열로. UTF-8 BOM."""
    if level not in drill.LEVEL_COL:
        raise HTTPException(status_code=400, detail=f"unknown level: {level}")
    data = drill.csv_bytes(level, f)
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="drill_{level}.csv"'})


@app.get("/api/target")
def target(month: str | None = None, date_from: str | None = None, date_to: str | None = None,
           stores_f: list[str] = Query(default=[], alias="store"),
           types_f: list[str] = Query(default=[], alias="type"),
           _: str = Depends(require_user), __: None = Depends(require_ready)):
    """목표 대비 실적 — 매장별 월 목표(gspread target_daily) vs 실판매(sales.gmv).
       전일(최근 실적일)·MTD 누계·예상마감(일 런레이트)·전월/전년동월 실적. store(매장)/type(채널) 필터만 반영.
       미매핑 shop은 fetch_targets INNER JOIN에서 이미 제외. target_daily 캐시 없으면 available=False."""
    import calendar as _cal
    import datetime as _dt
    smeta = store._meta_load()
    today = store.today_kst()
    smax = (smeta.get("sales_max_date") or today)[:10]
    # 오늘(장중 매시간 갱신되는 진행 중인 날)은 실적에서 제외 → '완료된 최근일'까지만 집계.
    # 전일·당월누계·예상마감 모두 이 기준으로 통일해 달성율/런레이트 왜곡 방지.
    eff_max = min(smax, (_dt.date.fromisoformat(today) - _dt.timedelta(days=1)).isoformat())
    try:
        months = store.query("SELECT DISTINCT strftime(CAST(sales_date AS DATE),'%Y-%m') m FROM target_daily ORDER BY m DESC")["m"].tolist()
    except Exception:
        months = []
    if not months:
        return {"available": False, "months": [], "month": None, "stores": [], "daily": [], "totals": {}}
    def _yr_ago(d):
        try:
            return d.replace(year=d.year - 1)
        except ValueError:
            return d - _dt.timedelta(days=365)
    if date_from and date_to:
        # 기간(일자 범위) 모드 — 월 대신 임의 [from,to]. 하위 집계는 m_start/m_end/last_actual/elapsed/
        # total_days/pm/py 만 참조하므로 이 변수들만 기간 기준으로 세팅하면 동일 계산 재사용.
        mode = "range"
        df_ = _dt.date.fromisoformat(date_from); dt_ = _dt.date.fromisoformat(date_to)
        if df_ > dt_:
            df_, dt_ = dt_, df_
        m_start, m_end = df_.isoformat(), dt_.isoformat()
        total_days = (dt_ - df_).days + 1
        has_actual = eff_max >= m_start
        last_actual = min(m_end, eff_max) if has_actual else m_start
        elapsed = ((_dt.date.fromisoformat(last_actual) - df_).days + 1) if has_actual else 0
        pm_e = df_ - _dt.timedelta(days=1); pm_s = pm_e - _dt.timedelta(days=total_days - 1)   # 직전 동일길이
        pm_start, pm_end = pm_s.isoformat(), pm_e.isoformat()
        py_start, py_end = _yr_ago(df_).isoformat(), _yr_ago(dt_).isoformat()                  # 전년 동기간
        month = f"{m_start} ~ {m_end}"
    else:
        mode = "month"
        if not month or month not in months:
            month = eff_max[:7] if eff_max[:7] in months else months[0]
        y, mo = int(month[:4]), int(month[5:7])
        total_days = _cal.monthrange(y, mo)[1]
        m_start, m_end = f"{month}-01", f"{month}-{total_days:02d}"
        pm_y, pm_mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
        pm_start, pm_end = f"{pm_y:04d}-{pm_mo:02d}-01", f"{pm_y:04d}-{pm_mo:02d}-{_cal.monthrange(pm_y, pm_mo)[1]:02d}"
        py_start, py_end = f"{y-1:04d}-{mo:02d}-01", f"{y-1:04d}-{mo:02d}-{_cal.monthrange(y-1, mo)[1]:02d}"
        has_actual = eff_max >= m_start
        last_actual = min(m_end, eff_max) if has_actual else m_start   # 전일=오늘(진행 중) 제외 최근 완료 실적일
        elapsed = int(last_actual[8:10]) if has_actual else 0          # 당월 경과 완료일수(예상마감 런레이트)

    cl, sp = [], []                                             # 매장/채널 필터 (target_daily·sales 공통 컬럼)
    if stores_f:
        cl.append("store_name IN (%s)" % ",".join(["?"] * len(stores_f))); sp += list(stores_f)
    if types_f:
        cl.append("shop_type IN (%s)" % ",".join(["?"] * len(types_f))); sp += list(types_f)
    sw = (" AND " + " AND ".join(cl)) if cl else ""

    tg = store.query(f"""
        SELECT store_name, ANY_VALUE(shop_type) shop_type, SUM(gmv_goal) goal_full,
               SUM(CASE WHEN d <= ? THEN gmv_goal ELSE 0 END) goal_mtd,
               SUM(CASE WHEN d  = ? THEN gmv_goal ELSE 0 END) goal_day
        FROM (SELECT store_name, shop_type, CAST(sales_date AS DATE) d, gmv_goal
              FROM target_daily WHERE CAST(sales_date AS DATE) BETWEEN ? AND ?{sw}) GROUP BY store_name
    """, [last_actual, last_actual, m_start, m_end, *sp])
    ac = store.query(f"""
        SELECT store_name, ANY_VALUE(shop_type) shop_type,
               SUM(CASE WHEN d <= ? THEN gmv ELSE 0 END) actual_mtd,
               SUM(CASE WHEN d  = ? THEN gmv ELSE 0 END) actual_day
        FROM (SELECT store_name, shop_type, CAST(sales_date AS DATE) d, gmv
              FROM sales WHERE CAST(sales_date AS DATE) BETWEEN ? AND ?{sw}) GROUP BY store_name
    """, [last_actual, last_actual, m_start, m_end, *sp])
    pm = store.query(f"SELECT store_name, SUM(gmv) v FROM sales WHERE CAST(sales_date AS DATE) BETWEEN ? AND ?{sw} GROUP BY store_name", [pm_start, pm_end, *sp])
    py = store.query(f"SELECT store_name, SUM(gmv) v FROM sales WHERE CAST(sales_date AS DATE) BETWEEN ? AND ?{sw} GROUP BY store_name", [py_start, py_end, *sp])

    g_full = {r.store_name: _num(r.goal_full) for r in tg.itertuples()}
    g_mtd = {r.store_name: _num(r.goal_mtd) for r in tg.itertuples()}
    g_day = {r.store_name: _num(r.goal_day) for r in tg.itertuples()}
    st_type = {r.store_name: r.shop_type for r in tg.itertuples()}
    a_mtd = {r.store_name: _num(r.actual_mtd) for r in ac.itertuples()}
    a_day = {r.store_name: _num(r.actual_day) for r in ac.itertuples()}
    for r in ac.itertuples():
        st_type.setdefault(r.store_name, r.shop_type)
    pm_a = {r.store_name: _num(r.v) for r in pm.itertuples()}
    py_a = {r.store_name: _num(r.v) for r in py.itertuples()}

    rows = []
    for nm in (set(g_full) | set(a_mtd)):
        gf, gm, gd = g_full.get(nm, 0), g_mtd.get(nm, 0), g_day.get(nm, 0)
        am, ad = a_mtd.get(nm, 0), a_day.get(nm, 0)
        proj = (am / elapsed * total_days) if elapsed else 0
        rows.append({"shop_type": st_type.get(nm, ""), "store_name": nm,
                     "goal_full": gf, "goal_day": gd, "actual_day": ad,
                     "rate_day": (ad / gd * 100) if gd else 0,
                     "goal_mtd": gm, "actual_mtd": am, "rate_mtd": (am / gm * 100) if gm else 0,
                     "proj": proj, "proj_rate": (proj / gf * 100) if gf else 0,
                     "pm_actual": pm_a.get(nm, 0), "py_actual": py_a.get(nm, 0)})
    rows.sort(key=lambda r: (-r["goal_full"], -r["actual_mtd"]))

    daily = store.query(f"""
        WITH g AS (SELECT CAST(sales_date AS DATE) d, SUM(gmv_goal) goal FROM target_daily
                   WHERE CAST(sales_date AS DATE) BETWEEN ? AND ?{sw} GROUP BY 1),
             a AS (SELECT CAST(sales_date AS DATE) d, CAST(SUM(gmv) AS DOUBLE) actual FROM sales
                   WHERE CAST(sales_date AS DATE) BETWEEN ? AND ?{sw} GROUP BY 1)
        SELECT CAST(COALESCE(g.d,a.d) AS STRING) d, COALESCE(g.goal,0) goal, a.actual
        FROM g FULL JOIN a ON g.d=a.d ORDER BY d
    """, [m_start, m_end, *sp, m_start, last_actual, *sp])
    _na = lambda v: None if (v is None or v != v) else round(float(v))
    daily_rows = [{"d": str(r.d)[:10], "goal": _num(r.goal), "actual": _na(r.actual)} for r in daily.itertuples()]

    def _t(k): return sum(r[k] for r in rows)
    tgf, tgm, tam, tgd, tad = _t("goal_full"), _t("goal_mtd"), _t("actual_mtd"), _t("goal_day"), _t("actual_day")
    tproj = (tam / elapsed * total_days) if elapsed else 0
    totals = {"goal_full": tgf, "goal_day": tgd, "actual_day": tad, "rate_day": (tad / tgd * 100) if tgd else 0,
              "goal_mtd": tgm, "actual_mtd": tam, "rate_mtd": (tam / tgm * 100) if tgm else 0,
              "proj": tproj, "proj_rate": (tproj / tgf * 100) if tgf else 0,
              "pm_actual": _t("pm_actual"), "py_actual": _t("py_actual")}
    return {"available": True, "mode": mode, "months": months, "month": month, "m_start": m_start, "m_end": m_end,
            "min_date": (months[-1] + "-01") if months else None, "max_date": eff_max,
            "last_actual": (last_actual if has_actual else None), "elapsed": elapsed, "total_days": total_days,
            "stores": rows, "daily": daily_rows, "totals": totals}


@app.get("/api/compare")
def compare(ref: str | None = None, clv: str = "대카테", f: dict = Depends(get_filters),
            _: str = Depends(require_user), __: None = Depends(require_ready)):
    """비교·신장율 탭 — 기준일(ref) 동기비(전일/전주/전월/전년). 날짜 필터는 기준일 윈도우로 대체."""
    f = {**f, "date_from": None, "date_to": None}   # 기간 필터 무시(ref 윈도우 사용)
    where, params = build_where(f)
    return cmptab.compute(ref, clv, where, params)


# --------------------------------------------------- 상품 메타 enrichment (스타일넘버·현재가·점별재고)
def _add_catalog(rows: list[dict]):
    """행에 style_no(스타일넘버)·normal_price(정상가)·sale_price(판매가) 부여 (goods_master 현재가)."""
    cat = prodmeta.goods_catalog([r["goods_no"] for r in rows])
    for r in rows:
        c = cat.get(r["goods_no"], {})
        r["style_no"] = c.get("style_no", "")
        r["normal_price"] = c.get("normal_price", 0)
        r["sale_price"] = c.get("sale_price", 0)
    return rows


def _add_store_stock_goods(rows: list[dict], store_cols: list[str]):
    """상품 행에 점별 재고 컬럼 부여(매장명 키) + 운영중인 매장수(점재고≥1 매장 수)."""
    stk = prodmeta.stock_by_goods([r["goods_no"] for r in rows])
    for r in rows:
        s = stk.get(r["goods_no"], {})
        op = 0
        for col in store_cols:
            v = s.get(col, 0)
            r[col] = v
            if (v or 0) >= 1:      # 점재고 1개 이상 = 운영중으로 판단
                op += 1
        r["op_stores"] = op
    return rows


def _add_brand_gmv(r: dict, f: dict) -> None:
    """brand_stock 행에 최근 28일 GMV·SOB(매출비중)·재고/매출 배수 병합 — GMV 대비 재고 과다 판단.
       재고비중(share, 전체 점재고 대비)과 SOB(gmv_share, 전체 GMV 대비)를 같은 브랜드 집합에서 비교.
       재고/매출 배수 = 재고비중 ÷ SOB (>1 재고과다·느린회전, <1 재고부족·빠른회전, 매출0 → None=∞)."""
    brand_rows = r.get("brand_stock") or []
    if not brand_rows:
        return
    import datetime as _dt
    d_to = _dt.date.today(); d_from = d_to - _dt.timedelta(days=28)
    f28 = {**f, "date_from": d_from.isoformat(), "date_to": d_to.isoformat()}
    where, params = build_where(f28)
    gmap = {}
    try:
        g = store.query(f"SELECT brand_nm, CAST(sum(gmv) AS DOUBLE) gmv FROM sales{where} GROUP BY brand_nm", params)
        gmap = {row.brand_nm: _num(row.gmv) for row in g.itertuples()}
    except Exception:
        gmap = {}
    tot_gmv = sum(gmap.values()) or 0.0
    for b in brand_rows:
        gv = gmap.get(b["name"], 0.0)
        b["gmv"] = gv
        b["gmv_share"] = round(gv / tot_gmv * 100, 1) if tot_gmv else 0.0
        b["over_index"] = round(b["share"] / b["gmv_share"], 2) if b["gmv_share"] else None
    r["brand_gmv_window"] = f"{d_from.isoformat()} ~ {d_to.isoformat()}"


@app.get("/api/inventory")
def inventory(f: dict = Depends(get_filters), limit: int = 1000,
              _: str = Depends(require_user), __: None = Depends(require_ready)):
    """재고 탭 — 최신 스냅샷. 공통 필터(사업구분·매장·브랜드·카테·MD·UID) 적용, 매장타입/매장으로 보이는 점재고 결정(기간 미적용).
       브랜드 표엔 최근 28일 GMV·SOB를 병합해 재고 과다 판단 지원."""
    r = invtab.compute(f, int(limit))
    if not r.get("empty") and r.get("rows"):
        _add_catalog(r["rows"])   # 스타일넘버·정상가·판매가 (#1, #4)
    if not r.get("empty"):
        _add_brand_gmv(r, f)      # 브랜드별 GMV·SOB·재고/매출 배수
    return r


@app.get("/api/inventory.csv")
def inventory_csv(f: dict = Depends(get_filters),
                  _: str = Depends(require_user), __: None = Depends(require_ready)):
    import io, csv
    header, rows = invtab.csv_rows(f)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    data = ("﻿" + buf.getvalue()).encode("utf-8")
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="offline_inventory_long.csv"'})


@app.get("/api/customer")
def customer(date_from: str | None = None, date_to: str | None = None,
             stores: list[str] = Query(default=[], alias="store"), type: list[str] = Query(default=[]),
             _: str = Depends(require_user), __: None = Depends(require_ready)):
    """고객·외국인 탭 — 인구통계(성별/연령/회원). 고객요약 테이블 기준(기간·매장·매장타입만 반영).
    주의: 파라미터명 stores (store는 import한 모듈명이라 섀도잉 금지)."""
    cl, params = [], []
    if date_from:
        cl.append("CAST(sales_date AS DATE) >= ?"); params.append(date_from)
    if date_to:
        cl.append("CAST(sales_date AS DATE) <= ?"); params.append(date_to)
    if stores:
        cl.append(f"store_name IN ({','.join(['?'] * len(stores))})"); params += list(stores)
    if type:
        cl.append(f"shop_type IN ({','.join(['?'] * len(type))})"); params += list(type)
    where = (" WHERE " + " AND ".join(cl)) if cl else ""

    def agg(col: str):
        df = store.query(f'SELECT {col} AS "name", CAST(sum(gmv) AS DOUBLE) gmv '
                         f"FROM customer{where} GROUP BY 1 ORDER BY gmv DESC", params)
        return [{"name": r.name, "gmv": _num(r.gmv)} for r in df.itertuples()]

    return {"sex": agg("sex"), "age": agg("age_band"), "member": agg("member")}


def _traffic_where(date_from, date_to, stores, types):
    cl, p = [], []
    if date_from:
        cl.append("sales_date >= CAST(? AS DATE)"); p.append(date_from)
    if date_to:
        cl.append("sales_date < CAST(? AS DATE) + INTERVAL 1 DAY"); p.append(date_to)
    if stores:
        cl.append("store_name IN (%s)" % ",".join(["?"] * len(stores))); p += list(stores)
    if types:
        cl.append("shop_type IN (%s)" % ",".join(["?"] * len(types))); p += list(types)
    return (" WHERE " + " AND ".join(cl)) if cl else "", p


@app.get("/api/footfall")
def footfall(date_from: str | None = None, date_to: str | None = None,
             stores: list[str] = Query(default=[], alias="store"), type: list[str] = Query(default=[]),
             _: str = Depends(require_user), __: None = Depends(require_ready)):
    """매장 입객수 + 구매전환율(=구매건수/입객). 기간·매장·매장타입 필터. footfall 캐시 없으면 available=False."""
    w, p = _traffic_where(date_from, date_to, stores, type)
    try:
        vis = store.query(f"SELECT store_name, ANY_VALUE(shop_type) shop_type, "
                          f"CAST(sum(visitors) AS DOUBLE) visitors FROM footfall{w} GROUP BY store_name", p)
    except Exception:
        return {"available": False, "rows": [], "totals": {}}
    rec = store.query(f"SELECT store_name, CAST(COUNT(DISTINCT order_id) AS DOUBLE) receipts, "
                      f"CAST(sum(gmv) AS DOUBLE) gmv FROM receipts{w} GROUP BY store_name", p)
    rmap = {r.store_name: (_num(r.receipts), _num(r.gmv)) for r in rec.itertuples()}
    rows = []
    for r in vis.itertuples():
        v = _num(r.visitors); rc, g = rmap.get(r.store_name, (0.0, 0.0))
        rows.append({"store_name": r.store_name, "shop_type": r.shop_type, "visitors": v,
                     "receipts": rc, "gmv": g,
                     "conversion": (rc / v * 100) if v else 0, "aov": (g / rc) if rc else 0})
    rows.sort(key=lambda x: -x["visitors"])
    tv = sum(x["visitors"] for x in rows); tr = sum(x["receipts"] for x in rows); tg = sum(x["gmv"] for x in rows)
    return {"available": True, "rows": rows,
            "totals": {"visitors": tv, "receipts": tr, "gmv": tg,
                       "conversion": (tr / tv * 100) if tv else 0, "aov": (tg / tr) if tr else 0}}


@app.get("/api/footfall/trend")
def footfall_trend(date_from: str | None = None, date_to: str | None = None,
                   stores: list[str] = Query(default=[], alias="store"), type: list[str] = Query(default=[]),
                   gran: str = "day", _: str = Depends(require_user), __: None = Depends(require_ready)):
    """입객수 추이 — 일/주/월 버킷. 기간·매장·매장타입 필터(트래픽 기준). footfall 캐시 없으면 available=False."""
    w, p = _traffic_where(date_from, date_to, stores, type)
    bucket = {"week": "CAST(date_trunc('week', sales_date) AS DATE)",
              "month": "CAST(date_trunc('month', sales_date) AS DATE)"}.get(gran, "CAST(sales_date AS DATE)")
    try:
        df = store.query(f"SELECT {bucket} AS bucket, CAST(sum(visitors) AS DOUBLE) visitors "
                         f"FROM footfall{w} GROUP BY 1 ORDER BY 1", p)
    except Exception:
        return {"available": False, "rows": []}
    return {"available": True,
            "rows": [{"bucket": str(r.bucket)[:10], "visitors": _num(r.visitors)} for r in df.itertuples()]}


@app.get("/api/customer/country")
def customer_country(date_from: str | None = None, date_to: str | None = None,
                     stores: list[str] = Query(default=[], alias="store"), type: list[str] = Query(default=[]),
                     limit: int = 20, _: str = Depends(require_user), __: None = Depends(require_ready)):
    """글로벌 고객 국가별 GMV(면세환급 국적 기준, gross). 기간·매장·매장타입 필터. 캐시 없으면 available=False."""
    w, p = _traffic_where(date_from, date_to, stores, type)
    try:
        df = store.query(f"SELECT nationality, CAST(sum(gmv) AS DOUBLE) gmv, "
                         f"CAST(sum(buyers) AS DOUBLE) buyers FROM global_customer{w} "
                         f"GROUP BY 1 ORDER BY gmv DESC", p)
    except Exception:
        return {"available": False, "rows": [], "total_gmv": 0, "countries": 0}
    tot = float(df["gmv"].sum()) if len(df) else 0.0
    rows = [{"nationality": r.nationality, "gmv": _num(r.gmv), "buyers": int(_num(r.buyers)),
             "share": (_num(r.gmv) / tot * 100) if tot else 0} for r in df.head(int(limit)).itertuples()]
    return {"available": True, "rows": rows, "total_gmv": tot, "countries": int(len(df))}


@app.get("/api/sales/brands")
def sales_brands(f: dict = Depends(get_filters),
                 _: str = Depends(require_user), __: None = Depends(require_ready)):
    """판매 탭 — 브랜드별 상세(사업구분×브랜드). 할인율·외국인비중 포함."""
    where, params = build_where(f)
    df = store.query(f"""
        SELECT business_type, brand_nm, CAST(sum(qty) AS DOUBLE) qty, CAST(sum(gmv) AS DOUBLE) gmv,
               CAST(sum(normal_amt) AS DOUBLE) normal_amt, CAST(sum(pay) AS DOUBLE) pay,
               CAST(sum(foreign_gmv) AS DOUBLE) foreign_gmv, count(DISTINCT goods_no) goods
        FROM sales{where} GROUP BY business_type, brand_nm HAVING sum(qty) > 0 ORDER BY gmv DESC""", params)
    out = []
    for r in df.itertuples():
        gmv, normal, fgn = _num(r.gmv), _num(r.normal_amt), _num(r.foreign_gmv)
        out.append({"business_type": r.business_type, "brand_nm": r.brand_nm, "qty": _num(r.qty),
                    "gmv": gmv, "normal_amt": normal, "pay": _num(r.pay), "foreign_gmv": fgn,
                    "goods": int(_num(r.goods)),
                    "discount_rate": (1 - gmv / normal) * 100 if normal else 0,
                    "foreign_ratio": (fgn / gmv * 100) if gmv else 0})
    # 순이익(Net Take)·공헌이익(CP) 브랜드 합 (settlement, editorial_summary_v)
    stl = _settlement_by_brand(f)
    for r in out:
        nc = stl.get((r["business_type"], r["brand_nm"]))
        r["net_take"] = nc[0] if nc else None
        r["cp"] = nc[1] if nc else None
    # 점별 재고(브랜드 합) 부여 (#2)
    store_cols = prodmeta.store_columns()
    stk = prodmeta.stock_by_brand([r["brand_nm"] for r in out])
    for r in out:
        s = stk.get(r["brand_nm"], {})
        for col in store_cols:
            r[col] = s.get(col, 0)
    return {"rows": out, "store_cols": store_cols}


def _goods_detail(f: dict, limit: int | None = None):
    """판매 탭 — 상품별 상세(판매 집계 + 상품 재고 점재고합계/허브합계)."""
    where, params = build_where(f)
    df = store.query(f"""
        SELECT business_type, cat_top, cat_large, cat_medium, brand_nm, goods_no,
               any_value(goods_nm) goods_nm, CAST(sum(qty) AS DOUBLE) qty, CAST(sum(gmv) AS DOUBLE) gmv,
               CAST(sum(normal_amt) AS DOUBLE) normal_amt, CAST(sum(pay) AS DOUBLE) pay,
               CAST(sum(foreign_gmv) AS DOUBLE) foreign_gmv
        FROM sales{where} GROUP BY business_type, cat_top, cat_large, cat_medium, brand_nm, goods_no
        HAVING sum(qty) > 0 ORDER BY gmv DESC{f" LIMIT {int(limit)}" if limit else ""}""", params)
    inv = store.get_inventory_goods()
    ic = daily._inv_cols(list(inv.columns))
    invs = inv[["goods_no", ic["store_stock"], ic["hub_total"]]].rename(
        columns={ic["store_stock"]: "jaego", ic["hub_total"]: "hub"})
    df = df.merge(invs, on="goods_no", how="left")
    df["jaego"] = df["jaego"].fillna(0)
    df["hub"] = df["hub"].fillna(0)
    stl = _settlement_by_goods(f)   # goods_no별 순이익(Net Take)·CP (settlement 캐시 없으면 {})
    out = []
    for r in df.itertuples():
        gmv, normal, fgn = _num(r.gmv), _num(r.normal_amt), _num(r.foreign_gmv)
        qv, pv = _num(r.qty), _num(r.pay)
        nt_cp = stl.get(int(r.goods_no))   # (net_take, cp) or None(미커버 → 화면 '—')
        out.append({"business_type": r.business_type, "cat_top": r.cat_top, "cat_large": r.cat_large,
                    "cat_medium": r.cat_medium, "brand_nm": r.brand_nm, "goods_no": int(r.goods_no),
                    "goods_nm": r.goods_nm, "qty": qv, "gmv": gmv, "normal_amt": normal,
                    "pay": pv, "foreign_gmv": fgn,
                    # 실판매단가 = 실제 팔린 평균 단가(세일 반영). bizest.goods.price(=정가)보다 정확.
                    "sale_unit": (gmv / qv) if qv else 0,
                    # 실결제단가 = 쿠폰·적립 반영 최종 결제 단가.
                    "pay_unit": (pv / qv) if qv else 0,
                    "discount_rate": (1 - gmv / normal) * 100 if normal else 0,
                    "foreign_ratio": (fgn / gmv * 100) if gmv else 0,
                    # 순이익(Net Take)·공헌이익(CP) — editorial_summary_v. 미커버 상품은 None(화면 '—').
                    "net_take": nt_cp[0] if nt_cp else None,
                    "cp": nt_cp[1] if nt_cp else None,
                    "jaego": _num(r.jaego), "hub": _num(r.hub)})
    return out


@app.get("/api/sales/goods")
def sales_goods(f: dict = Depends(get_filters), limit: int = 1500,
                _: str = Depends(require_user), __: None = Depends(require_ready)):
    rows = _goods_detail(f, limit)
    store_cols = prodmeta.store_columns()
    _add_catalog(rows)                          # 스타일넘버·정상가·판매가 (#1·#4)
    _add_store_stock_goods(rows, store_cols)    # 점별 재고 (#2)
    return {"rows": rows, "store_cols": store_cols}


def _goods_store_long(f: dict):
    """판매 CSV(long)용 — (매장 × 상품)별 판매지표 + 그 매장의 점재고. 화면 피벗(매장=열)을 행으로 푼 형태."""
    where, params = build_where(f)
    df = store.query(f"""
        SELECT store_name, business_type, cat_top, cat_large, cat_medium, brand_nm, goods_no,
               any_value(goods_nm) goods_nm, CAST(sum(qty) AS DOUBLE) qty, CAST(sum(gmv) AS DOUBLE) gmv,
               CAST(sum(normal_amt) AS DOUBLE) normal_amt, CAST(sum(pay) AS DOUBLE) pay,
               CAST(sum(foreign_gmv) AS DOUBLE) foreign_gmv
        FROM sales{where}
        GROUP BY store_name, business_type, cat_top, cat_large, cat_medium, brand_nm, goods_no
        HAVING sum(qty) <> 0 OR sum(gmv) <> 0 ORDER BY gmv DESC""", params)
    try:   # (goods_no × 매장) 점재고 조인
        stk = store.query('SELECT goods_no, store_name, CAST(sum("점재고") AS DOUBLE) stock '
                          "FROM inventory_store_long GROUP BY 1, 2")
        df = df.merge(stk, on=["goods_no", "store_name"], how="left")
    except Exception:
        df["stock"] = 0
    df["stock"] = df["stock"].fillna(0)
    try:   # (매장 × 상품) 순이익(Net Take)·공헌이익(CP) 조인 — editorial_summary_v
        ws, ps = build_where_settlement(f)
        stl = store.query(f"""SELECT store_name, goods_no, CAST(sum(net_take) AS DOUBLE) net_take,
            CAST(sum(cp) AS DOUBLE) cp FROM settlement{ws} GROUP BY store_name, goods_no""", ps)
        df = df.merge(stl, on=["store_name", "goods_no"], how="left")
    except Exception:
        df["net_take"] = 0.0; df["cp"] = 0.0
    df["net_take"] = df["net_take"].fillna(0.0)
    df["cp"] = df["cp"].fillna(0.0)
    return df


def _sales_option_where(f: dict):
    """sales_option(옵션단위 신선 판매) 공통필터 WHERE — 모든 컬럼 보유(so.* 직접 적용)."""
    cl, pr = [], []
    if f.get("date_from"):
        cl.append("so.sales_date >= CAST(? AS DATE)"); pr.append(f["date_from"])
    if f.get("date_to"):
        cl.append("so.sales_date < CAST(? AS DATE) + INTERVAL 1 DAY"); pr.append(f["date_to"])
    for key, col in (("biz", "business_type"), ("type", "shop_type"), ("store", "store_name"),
                     ("brand", "brand_nm"), ("cat_top", "cat_top"), ("cat_large", "cat_large"),
                     ("cat_medium", "cat_medium"), ("md", "off_md_id"), ("concept", "concept")):
        vals = f.get(key)
        if vals:
            cl.append(f"so.{col} IN ({','.join(['?'] * len(vals))})"); pr += list(vals)
    if f.get("goods"):
        g = [int(x) for x in f["goods"]]
        cl.append(f"so.goods_no IN ({','.join(['?'] * len(g))})"); pr += g
    if f.get("name_like"):
        cl.append("lower(so.goods_nm) LIKE ?"); pr.append("%" + str(f["name_like"]).lower() + "%")
    if f.get("brand_ex"):
        cl.append(f"so.brand_nm NOT IN ({','.join(['?'] * len(f['brand_ex']))})"); pr += list(f["brand_ex"])
    if f.get("running"):
        cl.append("so.is_running = 1")
    return (" WHERE " + " AND ".join(cl)) if cl else "", pr


def _settlement_option_df(f: dict):
    """CSV용 (매출일자 × 매장 × 상품 × 옵션) — 베이스=sales_option(신선, 오늘 포함),
       순이익(NetTake)·CP는 settlement_option(익일 반영) LEFT JOIN(최근분은 공란 가능) + 점재고.
       sales_option 캐시 없으면 None → 상위에서 goods 단위 폴백."""
    where, params = _sales_option_where(f)
    # 순이익/CP: 옵션명이 MOSS↔정산 간 표기 차이로 정확 매칭률이 낮아, goods(일자×매장×상품) 단위
    # 정산값을 옵션 GMV 비중으로 배분(전 옵션 커버 + goods 합계는 정산과 일치). 정산 미반영분(오늘 등)은 공란.
    # foreign_gmv(외국인GMV) 열은 신규 → 구 캐시(컬럼 없음)엔 0으로 폴백(fg 플래그).
    _cols_base = """so.sales_date, so.store_name, so.business_type, so.brand_nm,
                   so.cat_top, so.cat_large, so.cat_medium, so.goods_no, so.goods_nm, so.option_nm,
                   so.qty, so.gmv, so.normal_amt, so.pay, so.foreign_gmv"""

    def _so_cte(fg: bool) -> str:
        fexpr = "CAST(sum(so.foreign_gmv) AS DOUBLE)" if fg else "CAST(0 AS DOUBLE)"
        return f"""
            WITH so AS (
                SELECT so.sales_date, so.store_name, so.business_type, so.brand_nm,
                       so.cat_top, so.cat_large, so.cat_medium, so.goods_no,
                       any_value(so.goods_nm) goods_nm, so.option_nm,
                       CAST(sum(so.qty) AS DOUBLE) qty, CAST(sum(so.gmv) AS DOUBLE) gmv,
                       CAST(sum(so.normal_amt) AS DOUBLE) normal_amt, CAST(sum(so.pay) AS DOUBLE) pay,
                       {fexpr} foreign_gmv
                FROM sales_option so
                {where}
                GROUP BY so.sales_date, so.store_name, so.business_type, so.brand_nm,
                         so.cat_top, so.cat_large, so.cat_medium, so.goods_no, so.option_nm
                HAVING sum(so.qty) <> 0 OR sum(so.gmv) <> 0
            ),
            sog AS (SELECT sales_date, store_name, goods_no, sum(gmv) goods_gmv FROM so GROUP BY 1, 2, 3)"""

    def _q(fg: bool, settlement: bool) -> str:
        if settlement:
            return f"""{_so_cte(fg)},
                stg AS (SELECT sales_date, store_name, goods_no, sum(net_take) nt, sum(cp) cp FROM settlement_option GROUP BY 1, 2, 3)
                SELECT {_cols_base},
                       CAST(stg.nt * (so.gmv / NULLIF(sog.goods_gmv, 0)) AS DOUBLE) net_take,
                       CAST(stg.cp * (so.gmv / NULLIF(sog.goods_gmv, 0)) AS DOUBLE) cp
                FROM so
                JOIN sog ON sog.sales_date = so.sales_date AND sog.store_name = so.store_name AND sog.goods_no = so.goods_no
                LEFT JOIN stg ON stg.sales_date = so.sales_date AND stg.store_name = so.store_name AND stg.goods_no = so.goods_no
                ORDER BY so.sales_date, so.store_name, so.gmv DESC"""
        return f"""{_so_cte(fg)}
                SELECT {_cols_base}, CAST(NULL AS DOUBLE) net_take, CAST(NULL AS DOUBLE) cp
                FROM so JOIN sog ON sog.sales_date = so.sales_date AND sog.store_name = so.store_name AND sog.goods_no = so.goods_no
                ORDER BY so.sales_date, so.store_name, so.gmv DESC"""

    df = None
    for fg, stl in ((True, True), (True, False), (False, True), (False, False)):
        try:
            df = store.query(_q(fg, stl), params)
            break
        except Exception:
            df = None
    if df is None:
        return None   # sales_option 캐시 자체가 없을 때만 → goods 단위 폴백
    try:   # (goods_no × 매장) 점재고(goods 단위) 조인 — 옵션별로 반복 표기
        stk = store.query('SELECT goods_no, store_name, CAST(sum("점재고") AS DOUBLE) stock '
                          "FROM inventory_store_long GROUP BY 1, 2")
        df = df.merge(stk, on=["goods_no", "store_name"], how="left")
    except Exception:
        df["stock"] = 0
    df["stock"] = df["stock"].fillna(0)
    return df


@app.get("/api/sales/goods.csv")
def sales_goods_csv(f: dict = Depends(get_filters),
                    _: str = Depends(require_user), __: None = Depends(require_ready)):
    """판매 CSV — tidy/long. sales_option(신선, 오늘 포함) 있으면 (매출일자 × 매장 × 상품 × 옵션)당 1행
       (순이익·CP는 settlement goods단위를 GMV비중 배분), 캐시 없으면 (매장 × 상품) goods 단위로 폴백."""
    import io, csv, traceback
    try:
        return _sales_goods_csv_impl(f, io, csv)
    except Exception as e:
        logging.error("sales_goods_csv failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"CSV 생성 오류: {type(e).__name__}: {e}")


def _sales_goods_csv_impl(f: dict, io, csv):
    df = _settlement_option_df(f)
    buf = io.StringIO()
    w = csv.writer(buf)
    if df is not None and not df.empty:
        cat = prodmeta.goods_catalog([int(x) for x in df["goods_no"].unique().tolist()])
        w.writerow(["매출일자", "매장", "대카테", "중카테", "브랜드", "UID", "스타일넘버", "상품명", "옵션",
                    "정상가", "판매가(온라인)", "실판매가", "순판매수량", "GMV", "외국인GMV", "내국인GMV",
                    "정상가매출", "실결제", "순이익(NetTake)", "공헌이익(CP)", "점재고"])
        for r in df.itertuples():
            c = cat.get(int(r.goods_no), {})
            q = int(_num(r.qty))
            gmv, fgn = int(_num(r.gmv)), int(_num(getattr(r, "foreign_gmv", 0)))
            w.writerow([str(r.sales_date)[:10], r.store_name, r.cat_large, r.cat_medium, r.brand_nm,
                        int(r.goods_no), c.get("style_no", ""), r.goods_nm, r.option_nm,
                        int(c.get("normal_price", 0)), int(c.get("sale_price", 0)),
                        int(_num(r.gmv) / q) if q else 0,
                        q, gmv, fgn, gmv - fgn,   # 외국인(면세 tax_refund) / 내국인 = GMV − 외국인
                        int(_num(r.normal_amt)), int(_num(r.pay)),
                        int(_num(r.net_take)), int(_num(r.cp)), int(_num(r.stock))])
        fname = "offline_sales_by_date_option.csv"
    else:
        df = _goods_store_long(f)
        cat = prodmeta.goods_catalog([int(x) for x in df["goods_no"].tolist()]) if not df.empty else {}
        w.writerow(["매장", "사업구분", "최상위", "대카테", "중카테", "브랜드", "UID", "스타일넘버", "상품명",
                    "정상가", "판매가(온라인)", "실판매가", "순판매수량", "GMV", "정상가매출", "실결제", "외국인GMV",
                    "순이익(NetTake)", "공헌이익(CP)", "점재고"])
        for r in df.itertuples():
            c = cat.get(int(r.goods_no), {})
            q = int(_num(r.qty))
            w.writerow([r.store_name, r.business_type, r.cat_top, r.cat_large, r.cat_medium, r.brand_nm,
                        int(r.goods_no), c.get("style_no", ""), r.goods_nm,
                        int(c.get("normal_price", 0)), int(c.get("sale_price", 0)),
                        int(_num(r.gmv) / q) if q else 0,
                        q, int(_num(r.gmv)), int(_num(r.normal_amt)),
                        int(_num(r.pay)), int(_num(r.foreign_gmv)),
                        int(_num(r.net_take)), int(_num(r.cp)), int(_num(r.stock))])
        fname = "offline_sales_by_store_goods.csv"
    data = ("﻿" + buf.getvalue()).encode("utf-8")
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/sales/brands.csv")
def sales_brands_csv(f: dict = Depends(get_filters),
                     _: str = Depends(require_user), __: None = Depends(require_ready)):
    """브랜드별 상세 CSV — 매장별 long. (브랜드 × 매장)당 1행: 그 매장의 GMV·판매수량과 그 매장의 재고.
       GMV/판매는 기간필터 반영, 재고(점재고)는 스냅샷(카테/브랜드/상품명 필터의 goods만, 기간 무관)."""
    import io, csv, traceback
    try:
        where, psales = build_where(f)
        f_nodate = {**f, "date_from": None, "date_to": None}   # 재고 goods 스코프는 날짜 제외(현재고)
        wnd, pnd = build_where(f_nodate)
        df = store.query(f"""
            WITH s AS (
                SELECT business_type, brand_nm, store_name,
                       CAST(sum(qty) AS DOUBLE) qty, CAST(sum(gmv) AS DOUBLE) gmv,
                       CAST(sum(normal_amt) AS DOUBLE) normal_amt, CAST(sum(pay) AS DOUBLE) pay,
                       CAST(sum(foreign_gmv) AS DOUBLE) foreign_gmv
                FROM sales{where} GROUP BY 1, 2, 3 HAVING sum(qty) <> 0 OR sum(gmv) <> 0
            ),
            bbiz AS (SELECT brand_nm, any_value(business_type) bt FROM s GROUP BY brand_nm),
            gset AS (SELECT DISTINCT goods_no FROM sales{wnd}),
            dmap AS (SELECT DISTINCT goods_no, brand_nm FROM sales),
            k AS (
                SELECT dmap.brand_nm, i.store_name, CAST(sum(i."점재고") AS DOUBLE) stock
                FROM inventory_store_long i
                JOIN dmap ON dmap.goods_no = i.goods_no
                WHERE i.goods_no IN (SELECT goods_no FROM gset)
                  AND dmap.brand_nm IN (SELECT brand_nm FROM s)
                GROUP BY 1, 2
            )
            SELECT COALESCE(bbiz.bt, '') business_type,
                   COALESCE(s.brand_nm, k.brand_nm) brand_nm,
                   COALESCE(s.store_name, k.store_name) store_name,
                   COALESCE(s.qty, 0) qty, COALESCE(s.gmv, 0) gmv, COALESCE(s.normal_amt, 0) normal_amt,
                   COALESCE(s.pay, 0) pay, COALESCE(s.foreign_gmv, 0) foreign_gmv,
                   COALESCE(k.stock, 0) stock
            FROM s FULL OUTER JOIN k ON k.brand_nm = s.brand_nm AND k.store_name = s.store_name
            LEFT JOIN bbiz ON bbiz.brand_nm = COALESCE(s.brand_nm, k.brand_nm)
            ORDER BY brand_nm, gmv DESC, store_name""", psales + pnd)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["사업구분", "브랜드", "매장", "순판매수량", "GMV", "정상가매출", "실결제",
                    "외국인GMV", "할인율%", "점재고"])
        for r in df.itertuples():
            gmv, normal = _num(r.gmv), _num(r.normal_amt)
            w.writerow([r.business_type, r.brand_nm, r.store_name, int(_num(r.qty)), int(gmv), int(normal),
                        int(_num(r.pay)), int(_num(r.foreign_gmv)),
                        round((1 - gmv / normal) * 100, 1) if normal else 0, int(_num(r.stock))])
        data = ("﻿" + buf.getvalue()).encode("utf-8")
        return Response(content=data, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="offline_sales_by_brand_store.csv"'})
    except Exception as e:
        logging.error("sales_brands_csv failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"브랜드 CSV 오류: {type(e).__name__}: {e}")


@app.get("/api/pnl")
def pnl(mode: str = "month", period: str | None = None, level: str = "store",
        date_from: str | None = None, date_to: str | None = None,
        store_f: list[str] = Query(default=[], alias="store"),
        types_f: list[str] = Query(default=[], alias="type"),
        brand_f: list[str] = Query(default=[], alias="brand"),
        _: str = Depends(require_user), __: None = Depends(require_ready)):
    """손익(P&L) — 매장별(level=store) 또는 브랜드별(level=brand, 매장 필터) × 월마감/일마감/기간.
       공식 정산값(editorial): Net Take=profit, CP=contribution_profit_pre, GMV=정산(ord_amt),
       매장고정비=offline_cost. 당기 + 직전기간·전년동기간 CP 대비. settlement_daily 캐시 필요.
       mode=range + date_from/date_to → 임의 기간(직전 동일길이·전년동기간과 비교)."""
    import datetime as _dt, calendar as _cal
    try:
        mrow = store.query("SELECT CAST(max(sales_date) AS DATE) mx, CAST(min(sales_date) AS DATE) mn FROM settlement_daily").iloc[0]
        max_d, min_d = mrow["mx"], mrow["mn"]
    except Exception:
        return {"available": False, "rows": [], "months": [], "mode": mode}
    if max_d is None:
        return {"available": False, "rows": [], "months": [], "mode": mode}
    max_d = _dt.date.fromisoformat(str(max_d)[:10]); min_d = _dt.date.fromisoformat(str(min_d)[:10])

    def mrange(y, m):
        return _dt.date(y, m, 1), _dt.date(y, m, _cal.monthrange(y, m)[1])

    def yr_ago(d):
        try:
            return d.replace(year=d.year - 1)
        except ValueError:              # 2/29 → 전년 없음
            return d - _dt.timedelta(days=365)
    if mode == "range":
        # 임의 기간. 미지정 시 최신월 1일~max_d. from>to면 스왑.
        cf = _dt.date.fromisoformat(date_from) if date_from else max_d.replace(day=1)
        ct = _dt.date.fromisoformat(date_to) if date_to else max_d
        if cf > ct:
            cf, ct = ct, cf
        cur = (cf, ct)
        length = (ct - cf).days                     # 직전 동일길이 구간(바로 앞)
        pm = (cf - _dt.timedelta(days=length + 1), cf - _dt.timedelta(days=1))
        py = (yr_ago(cf), yr_ago(ct))               # 전년 동기간
        label = f"{cf.isoformat()} ~ {ct.isoformat()}"
    elif mode == "day":
        cd = _dt.date.fromisoformat(period) if period else max_d
        cur, pm, py = (cd, cd), (cd - _dt.timedelta(days=1),) * 2, (cd.replace(year=cd.year - 1),) * 2
        label = cd.isoformat()
    else:
        y, m = (int(period[:4]), int(period[5:7])) if period else (max_d.year, max_d.month)
        cur = mrange(y, m)
        pm = mrange(y - 1, 12) if m == 1 else mrange(y, m - 1)
        py = mrange(y - 1, m)
        label = f"{y:04d}-{m:02d}"

    gcol = "brand_nm" if level == "brand" else "store_name"
    wc, wp = [], []
    for vals, col in ((types_f, "shop_type"), (store_f, "store_name"), (brand_f, "brand_nm")):
        if vals:
            wc.append(f"{col} IN ({','.join(['?'] * len(vals))})"); wp += list(vals)
    wextra = (" AND " + " AND ".join(wc)) if wc else ""

    def agg(rng, cols):
        return store.query(f"""
            SELECT {gcol} k, {cols} FROM settlement_daily
            WHERE sales_date >= CAST(? AS DATE) AND sales_date < CAST(? AS DATE) + INTERVAL 1 DAY{wextra}
            GROUP BY {gcol}""", [rng[0].isoformat(), rng[1].isoformat()] + wp)
    cur_df = agg(cur, "CAST(sum(gmv) AS DOUBLE) gmv, CAST(sum(net_take) AS DOUBLE) net_take, "
                      "CAST(sum(cp) AS DOUBLE) cp, CAST(sum(offline_cost) AS DOUBLE) offline_cost, "
                      "CAST(sum(normal_amt) AS DOUBLE) normal_amt, CAST(sum(qty) AS DOUBLE) qty, "
                      "any_value(shop_type) shop_type")
    pmm = {r.k: _num(r.cp) for r in agg(pm, "CAST(sum(cp) AS DOUBLE) cp").itertuples()}
    pym = {r.k: _num(r.cp) for r in agg(py, "CAST(sum(cp) AS DOUBLE) cp").itertuples()}
    _d = lambda a, b: ((a - b) / abs(b) * 100) if b else None

    rows = []
    for r in cur_df.itertuples():
        gmv, cp, nt = _num(r.gmv), _num(r.cp), _num(r.net_take)
        pmc, pyc = pmm.get(r.k, 0.0), pym.get(r.k, 0.0)
        rows.append({"name": r.k, "shop_type": getattr(r, "shop_type", ""),
                     "gmv": gmv, "net_take": nt, "cp": cp, "offline_cost": _num(r.offline_cost),
                     "qty": _num(r.qty), "normal_amt": _num(r.normal_amt),
                     "cp_rate": (cp / (gmv / 1.1) * 100) if gmv else 0,
                     "nt_rate": (nt / gmv * 100) if gmv else 0,
                     "pm_cp": pmc, "pm_delta": _d(cp, pmc), "py_cp": pyc, "py_delta": _d(cp, pyc)})
    rows.sort(key=lambda x: -x["cp"])

    def T(k): return sum(x[k] for x in rows)
    tg, tcp, tnt, tpm, tpy = T("gmv"), T("cp"), T("net_take"), T("pm_cp"), T("py_cp")
    totals = {"name": "합계", "shop_type": "", "gmv": tg, "net_take": tnt, "cp": tcp,
              "offline_cost": T("offline_cost"), "qty": T("qty"), "normal_amt": T("normal_amt"),
              "cp_rate": (tcp / (tg / 1.1) * 100) if tg else 0, "nt_rate": (tnt / tg * 100) if tg else 0,
              "pm_cp": tpm, "pm_delta": _d(tcp, tpm), "py_cp": tpy, "py_delta": _d(tcp, tpy)}

    # 월 목록(월마감 셀렉트용)
    months = []
    yy, mm = min_d.year, min_d.month
    while (yy, mm) <= (max_d.year, max_d.month):
        months.append(f"{yy:04d}-{mm:02d}")
        mm = 1 if mm == 12 else mm + 1
        yy = yy + 1 if mm == 1 else yy
    # 잠정: 당기 종료일이 최근 ~2개월 이내면 CP·고정비 예측(미확정)
    provisional = cur[1] >= (max_d.replace(day=1) - _dt.timedelta(days=62))
    return {"available": True, "mode": mode, "level": level, "period": label,
            "rows": rows, "totals": totals, "provisional": provisional,
            "months": months[::-1], "max_date": max_d.isoformat(), "min_date": min_d.isoformat(),
            "range": {"from": cur[0].isoformat(), "to": cur[1].isoformat()}}


@app.get("/api/daily")
def daily_report(basis: str | None = None, seg: str | None = None,
                 _: str = Depends(require_user), __: None = Depends(require_ready)):
    """요약 탭 — 최신 데이터일 기준 일별 리포트. basis=최상위카테, seg=매입/위탁 기준으로 재계산.
    (전일 종합·액션포인트·주목상품 4일·매장/브랜드/상품 TOP100·재고보충 매장×상품)."""
    return daily.report(basis, seg)


@app.get("/api/daily/restock.csv")
def daily_restock_csv(basis: str | None = None, seg: str | None = None,
                      _: str = Depends(require_user), __: None = Depends(require_ready)):
    """재고보충 필요 상품 전체 CSV (캡 없음) — 허브 발주용. UTF-8 BOM."""
    import io, csv
    d0, rows = daily.restock_full(basis, seg)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["매장", "브랜드", "UID", "상품명", "전일판매", "점재고", "허브재고"])
    for r in rows:
        w.writerow([r["store"], r["brand"], r["goods_no"], r["name"],
                    int(r["sold"]), int(r["store_stock"]), int(r["hub_total"])])
    data = ("﻿" + buf.getvalue()).encode("utf-8")     # BOM → Excel 한글 정상
    fname = f"restock_{d0 or 'empty'}.csv"
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# 통합 IPS — 브랜드×구분(매입/위탁) 단일 뷰. 공통 필터 미적용(독립 탭). CP는 Phase1에서 숨김.
_IPS_ID_COLS = ("sil", "gubun", "brand_code", "com_id", "brand_nm")
# 합계에서 단순 합산하면 안 되는 파생지표(비율/일수) — 합계행에서 재계산.
_IPS_DERIVED = {"sell_through", "days_all", "days_off", "normal_price"}


def _ips_week_labels():
    """주차 라벨(W0=직전 완료주 월~일). Spark DATE_TRUNC('WEEK')=월요일 시작과 동일하게 계산."""
    import datetime as _dt
    today = _dt.date.fromisoformat(store.today_kst())
    ws = today - _dt.timedelta(days=today.weekday())   # 이번 주 월요일
    out = {}
    for i in range(4):
        s = ws - _dt.timedelta(days=7 * (i + 1))
        e = ws - _dt.timedelta(days=7 * i + 1)
        out[f"w{i}"] = f"{s.strftime('%m/%d')}~{e.strftime('%m/%d')}"
    return out


@app.get("/api/ips")
def ips(_: str = Depends(require_user), __: None = Depends(require_ready)):
    """통합 IPS 브랜드×구분 스냅샷. 재고(전체/매장/물류)+입고 + 4주 판매(수량/GMV/NetTake, 전체/온/오프)
       + 셀스루·예상일수. 원천: orders_merged/editorial_summary/sku_stock_history. ips 캐시 필요."""
    try:
        df = store.query("SELECT * FROM ips")
    except Exception:
        return {"available": False, "rows": []}
    if df is None or df.empty:
        return {"available": False, "rows": []}
    num_cols = [c for c in df.columns if c not in _IPS_ID_COLS]
    rows = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        row = {k: (str(d.get(k) or "")) for k in _IPS_ID_COLS}
        for c in num_cols:
            v = d.get(c)
            # 비율/일수는 null 허용(계산 불가 표시), 나머지는 0으로.
            if c in _IPS_DERIVED:
                fv = float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None
                row[c] = fv
            else:
                row[c] = _num(v)
        rows.append(row)
    rows.sort(key=lambda x: -(x.get("gmv_tot_w0", 0) + x.get("gmv_tot_w1", 0)
                              + x.get("gmv_tot_w2", 0) + x.get("gmv_tot_w3", 0)))

    # 합계행 — 합산 가능한 컬럼만 합산, 파생지표는 합계 기준 재계산.
    tot = {k: "" for k in _IPS_ID_COLS}
    tot["brand_nm"] = "합계"
    for c in num_cols:
        if c not in _IPS_DERIVED:
            tot[c] = sum(x.get(c, 0) or 0 for x in rows)
    q4 = tot["qty_tot_w0"] + tot["qty_tot_w1"] + tot["qty_tot_w2"] + tot["qty_tot_w3"]
    off4 = tot["qty_off_w0"] + tot["qty_off_w1"] + tot["qty_off_w2"] + tot["qty_off_w3"]
    tc = tot["total_cur"]
    tot["sell_through"] = round(q4 / (q4 + tc) * 100, 1) if (q4 + tc) else None
    tot["days_all"] = round(tc / (q4 / 28.0), 1) if q4 else None
    tot["days_off"] = round(tot["store_cur"] / (off4 / 28.0), 1) if off4 else None
    tot["normal_price"] = None

    data_date = store._mtime_kst("ips") or ""
    return {"available": True, "rows": rows, "totals": tot,
            "weeks": _ips_week_labels(), "refreshed_at": data_date[:16]}


@app.get("/api/ips/goods")
def ips_goods(brand_code: str = Query(...), gubun: str = Query(...), limit: int = 2000,
              _: str = Depends(require_user), __: None = Depends(require_ready)):
    """IPS 상품 드릴다운 — 특정 브랜드×구분의 상품(goods_no) 단위 재고+4주판매+입고예정.
       ips_goods 캐시에서 필터. GMV 4주합 내림차순."""
    try:
        df = store.query(
            "SELECT * FROM ips_goods WHERE brand_code = ? AND gubun = ? ORDER BY gmv_tot DESC LIMIT ?",
            [brand_code, gubun, int(limit)])
    except Exception:
        return {"available": False, "rows": []}
    id_cols = ("brand_code", "gubun", "product_no", "goods_nm", "brand_nm")
    der = {"sell_through", "days_all"}
    rows = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        row = {k: str(d.get(k) or "") for k in id_cols}
        for c in df.columns:
            if c in id_cols:
                continue
            v = d.get(c)
            if c in der:
                row[c] = float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None
            else:
                row[c] = _num(v)
        rows.append(row)
    return {"available": True, "rows": rows}


# ----------------------------------------------------------------- 빌드된 React SPA 서빙 (단일 포트)
# 모든 /api 라우트 뒤에 등록 → /api/* 가 우선 매칭. web/dist 가 있으면 같은 origin으로 앱+API 제공.
DIST = os.path.join(APP_DIR, "web", "dist")
if os.path.isdir(DIST):
    @app.get("/{full_path:path}")
    def spa(full_path: str = ""):
        fp = os.path.join(DIST, full_path)
        if full_path and os.path.isfile(fp):
            return FileResponse(fp)               # 정적 자산(assets, manifest 등)
        return FileResponse(os.path.join(DIST, "index.html"))   # SPA fallback
