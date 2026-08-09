"""Databricks 접속 + 판매/재고 데이터 1회 로딩.

성능 전략: 무거운 통합 쿼리는 세션당 1번만 실행해 집계된 DataFrame을 캐시.
이후 필터/집계/신장율은 전부 pandas에서 즉시 처리.
"""
from __future__ import annotations

import os

try:
    import truststore
    truststore.inject_into_ssl()  # 회사 프록시 TLS (Windows 인증서)
except Exception:
    pass

import pandas as pd
import streamlit as st
from databricks import sql


def _new_connection():
    # 배포(서버/Cloud Run): 환경변수 우선. 로컬: .streamlit/secrets.toml 폴백.
    host = os.environ.get("DATABRICKS_HOST")
    http_path = os.environ.get("DATABRICKS_HTTP_PATH")
    token = os.environ.get("DATABRICKS_TOKEN")
    if not (host and http_path and token):
        c = st.secrets["databricks"]
        host, http_path, token = c["host"], c["http_path"], c["token"]
    # use_cloud_fetch=False: 대용량 결과를 CDN(urllib3)에서 받지 않고 SQL 엔드포인트로 직접 수신.
    # 회사 프록시 truststore가 CloudFetch 다운로드 TLS(verify_mode/check_hostname)와 충돌해
    # 간헐적 'Cannot set verify_mode to CERT_NONE' 오류가 나던 것을 방지 (집계 쿼리라 성능 영향 미미).
    return sql.connect(server_hostname=host, http_path=http_path, access_token=token,
                       use_cloud_fetch=False)


@st.cache_resource(show_spinner=False)
def _connection():
    return _new_connection()


def _reset_connection():
    """끊긴 세션(웨어하우스 idle 타임아웃 등)을 폐기하고 다음 호출 때 새로 연결."""
    try:
        old = _connection()
    except Exception:
        old = None
    try:
        _connection.clear()   # st.cache_resource 캐시 비우기
    except Exception:
        pass
    if old is not None:
        try:
            old.close()
        except Exception:
            pass


def _is_stale_session(e: Exception) -> bool:
    m = str(e).lower()
    return ("sessionhandle" in m or "session is closed" in m or "expired" in m
            or "connection is closed" in m or ("session" in m and "closed" in m))


def _execute(query: str) -> pd.DataFrame:
    conn = _connection()
    with conn.cursor() as cur:
        cur.execute(query)
        # arrow→pandas: 컬럼형 변환이라 대용량(예: goods_master 600만행)도 메모리 효율적.
        # (fetchall()은 행마다 ResultRow 객체를 만들어 대용량에서 MemoryError 발생)
        try:
            return cur.fetchall_arrow().to_pandas()
        except Exception:
            cols = [d[0] for d in cur.description]
            return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)


def run_df(query: str) -> pd.DataFrame:
    try:
        return _execute(query)
    except Exception as e:
        if not _is_stale_session(e):
            raise
        # 세션이 닫힘 → 재연결 후 1회 재시도 (캐시된 끊긴 연결 자동 복구)
        _reset_connection()
        return _execute(query)


# 공통: 통합 매장 차원 (15개)
DIM_STORE = r"""
dim_store AS (
  SELECT t.shop_no,
    CASE WHEN t.shop_no = 90 THEN 'RUN' ELSE UPPER(t.shop_type) END AS shop_type,
    REGEXP_REPLACE(TRIM(COALESCE(sid.shop_name, t.shop_nm)), ' +', ' ') AS store_name
  FROM (SELECT DISTINCT CAST(shop_no AS INT) AS shop_no, shop_type, shop_nm
        FROM team.sales.offline_sales_mart_v
        WHERE shop_type IN ('selectshop','kicks','beauty','outlet')) t
  LEFT JOIN team.commercepm.offline_shopno_storageid sid ON CAST(sid.shop_no AS INT) = t.shop_no
)
"""


# ---------------------------------------------------------------------------
# 판매 fact (MOSS) — 외국인 매출 포함
# ---------------------------------------------------------------------------
def sales_latest_ts() -> str | None:
    """판매 데이터(오프라인 완료주문)의 가장 최근 거래 시각 = MAX(transaction_at, 없으면 created_at).
    fetch_sales의 ord 범위(dummy_order=0, order_status=50, dim_store 오프라인)와 동일."""
    q = ("WITH " + DIM_STORE + r"""
        SELECT CAST(MAX(COALESCE(om.transaction_at, om.created_at)) AS STRING) ts
        FROM ocmp.moss.order_master om
        JOIN dim_store st ON st.shop_no = om.shop_no
        WHERE om.dummy_order = 0 AND om.order_status = 50
    """)
    try:
        v = run_df(q).iloc[0, 0]
        return str(v) if v is not None else None
    except Exception:
        return None


def fetch_sales(since: str | None = None) -> pd.DataFrame:
    """주문원장(order_master+order_option+claim)에서 판매 fact 집계.
    since='YYYY-MM-DD'를 주면 그 날짜 이후 주문/환불만(증분 갱신용). None=전체 히스토리."""
    ordf = f"AND CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) >= '{since}'" if since else ""
    reff = f"AND CAST(created_at AS DATE) >= '{since}'" if since else ""
    q = (
        "WITH " + DIM_STORE + r""",
        dim_brand AS (
          SELECT cb.com_id, cb.brand AS brand_code, b.brand_nm,
            CASE c.margin_type WHEN 'FEE' THEN '위탁' WHEN 'WONGA' THEN '매입' ELSE '기타' END AS business_type
          FROM musinsa.partnerportal.company_brand cb
          LEFT JOIN musinsa.partnerportal.brand   b ON b.brand  = cb.brand
          LEFT JOIN musinsa.partnerportal.company c ON c.com_id = cb.com_id
        ),
        catmap AS (
          SELECT goods_no,
                 ANY_VALUE(final_large_nm_off) AS cat_top,
                 ANY_VALUE(large_nm_off)       AS cat_large,
                 ANY_VALUE(medium_nm_off)      AS cat_medium,
                 ANY_VALUE(offline_md_id)      AS off_md_id
          FROM team.sales.dsh_d_upt_editorial_stock_summary s2
          JOIN (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary) lc
            ON s2.ord_state_date = lc.d
          GROUP BY goods_no
        ),
        -- 총주문 라인 (양수). 판매일 = transaction_at(없으면 created_at). order_status=50=유효완료.
        --   ※ offline_sales_master(4월 스냅샷 뷰)는 현재 중복 부풀림 + 과거 없음 → 주문 원장에서 직접 구축.
        --     주문원장은 created_at 기준 2023-10+ 전체 히스토리 보유.
        ord AS (
          SELECT CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) AS sales_date,
                 om.shop_no, oo.goods_no, oo.company_id, oo.brand_id, om.tax_refund_type,
                 1 AS sgn, oo.quantity, oo.order_amount, oo.normal_amount, oo.pay_amount
          FROM ocmp.moss.order_option oo
          JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
          WHERE om.dummy_order = 0 AND om.order_status = 50 """ + ordf + r"""
        ),
        -- 환불 (음수). claim_type=REFUND를 주문라인에 귀속, 환불 처리일(claim.created_at) 기준.
        --   (검증: 2026-06-21 총주문−환불 = 765,922,600/20,649 로 기존 MOSS 검증값과 정확히 일치. 교환은 수량중립이라 제외.)
        refclaim AS (
          SELECT order_id, CAST(created_at AS DATE) AS refund_date
          FROM ocmp.moss.claim
          WHERE claim_type = 'REFUND' """ + reff + r"""
          GROUP BY order_id, CAST(created_at AS DATE)
        ),
        ref AS (
          SELECT rc.refund_date AS sales_date,
                 om.shop_no, oo.goods_no, oo.company_id, oo.brand_id, om.tax_refund_type,
                 -1 AS sgn, oo.quantity, oo.order_amount, oo.normal_amount, oo.pay_amount
          FROM refclaim rc
          JOIN ocmp.moss.order_master om ON om.order_id = rc.order_id
          JOIN ocmp.moss.order_option oo ON oo.order_id = rc.order_id
          WHERE om.dummy_order = 0
        ),
        lines AS (SELECT * FROM ord UNION ALL SELECT * FROM ref),
        fact AS (
          SELECT
            l.sales_date,
            st.store_name, st.shop_type,
            COALESCE(br.brand_nm, '(미매칭)')   AS brand_nm,
            COALESCE(br.business_type, '기타')  AS business_type,
            CAST(l.goods_no AS BIGINT) AS goods_no,
            cat.cat_top, cat.cat_large, cat.cat_medium, cat.off_md_id,
            l.company_id, l.brand_id, l.sgn,
            (CASE WHEN l.tax_refund_type IS NOT NULL AND l.tax_refund_type <> 'NONE' THEN 1 ELSE 0 END) AS is_foreign,
            l.quantity, l.order_amount, l.normal_amount, l.pay_amount
          FROM lines l
          JOIN dim_store st ON st.shop_no = l.shop_no
          LEFT JOIN dim_brand br ON br.com_id = l.company_id AND br.brand_code = l.brand_id
          LEFT JOIN catmap cat ON cat.goods_no = l.goods_no
        )
        SELECT sales_date, store_name, shop_type, business_type, brand_nm, goods_no,
               cat_top, cat_large, cat_medium, off_md_id, company_id, brand_id,
               CAST(SUM(sgn*quantity)                AS DOUBLE) AS qty,
               CAST(SUM(sgn*order_amount)            AS DOUBLE) AS gmv,
               CAST(SUM(sgn*normal_amount)           AS DOUBLE) AS normal_amt,
               CAST(SUM(sgn*pay_amount)              AS DOUBLE) AS pay,
               CAST(SUM(sgn*order_amount*is_foreign) AS DOUBLE) AS foreign_gmv
        FROM fact
        GROUP BY sales_date, store_name, shop_type, business_type, brand_nm, goods_no,
                 cat_top, cat_large, cat_medium, off_md_id, company_id, brand_id
        """
    )
    df = run_df(q)
    df["sales_date"] = pd.to_datetime(df["sales_date"])
    df["goods_no"] = pd.to_numeric(df["goods_no"]).astype("int64")
    for c in ("qty", "gmv", "normal_amt", "pay", "foreign_gmv"):
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    # 상품 마스터(bizest.goods)로 상품명 + 브랜드명 보강 (트레이딩/위탁 누락 방지)
    gm = load_goods_master()
    df = df.merge(gm, on="goods_no", how="left", suffixes=("", "_gm"))
    df["brand_nm"] = df["brand_nm_gm"].replace("", None).fillna(df["brand_nm"]).fillna("(미매칭)")
    df["goods_nm"] = df["goods_nm"].fillna("")
    df = df.drop(columns=["brand_nm_gm"])
    for c in ("cat_top", "cat_large", "cat_medium"):
        df[c] = df[c].fillna("미분류")
    df["off_md_id"] = df["off_md_id"].fillna("").astype(str)
    # 쇼핑백(수베니어샵)은 순판매수량에서 제외 — qty=0 처리(매출 gmv·정상가 등은 유지). 전 지표/탭 일관 반영.
    df.loc[df["brand_nm"] == "수베니어샵", "qty"] = 0.0
    return df  # concept는 app에서 load_concept_map으로 매핑(뷰 불안정 분리)


def fetch_receipts(since: str | None = None) -> pd.DataFrame:
    """객단가용 영수증(주문) 단위 집계. 그레인 = 주문ID × 판매일 × 매장 × 매장타입 × 내외국인
       × 사업구분 × 카테(최상위/대/중) × 브랜드.  → 카테/브랜드/사업구분 필터별 객단가 지원.
       객단가 = SUM(order_amount)(분자) / COUNT(DISTINCT order_id)(분모).
       ※ order_id 단위로 저장하고 분모를 COUNT(DISTINCT)로 세므로, 한 주문이 여러 카테/브랜드에
         걸쳐 여러 행이 돼도 전체/멀티선택 객단가가 중복합산 없이 정확히 보존된다(검증 2026-06-29:
         전체 객단가 신=구 93,112 일치).
       모집단 = 완료주문(dummy_order=0, status=50, 오프라인). 환불 미반영(영수증 시점 금액).
       브랜드/카테 해석은 fetch_sales와 동일(brand=goods_master 우선→dim_brand, cat=catmap).
       since='YYYY-MM-DD' 주면 증분(그 날짜 이후 거래)."""
    ordf = f"AND CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) >= '{since}'" if since else ""
    q = "WITH " + DIM_STORE + r""",
        dim_brand AS (   -- fetch_sales와 동일
          SELECT cb.com_id, cb.brand AS brand_code, b.brand_nm,
            CASE c.margin_type WHEN 'FEE' THEN '위탁' WHEN 'WONGA' THEN '매입' ELSE '기타' END AS business_type
          FROM musinsa.partnerportal.company_brand cb
          LEFT JOIN musinsa.partnerportal.brand   b ON b.brand  = cb.brand
          LEFT JOIN musinsa.partnerportal.company c ON c.com_id = cb.com_id
        ),
        catmap AS (   -- fetch_sales와 동일(최신 스냅샷 카테)
          SELECT goods_no, ANY_VALUE(final_large_nm_off) AS cat_top,
                 ANY_VALUE(large_nm_off) AS cat_large, ANY_VALUE(medium_nm_off) AS cat_medium
          FROM team.sales.dsh_d_upt_editorial_stock_summary s2
          JOIN (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary) lc
            ON s2.ord_state_date = lc.d
          GROUP BY goods_no
        ),
        ord AS (
          SELECT om.order_id,
                 CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) AS sales_date,
                 HOUR(COALESCE(om.transaction_at, om.created_at)) AS hour,   -- 거래시각 시(KST) — 시간대별 매출용
                 om.shop_no, oo.goods_no, oo.company_id, oo.brand_id,
                 (CASE WHEN om.tax_refund_type IS NOT NULL AND om.tax_refund_type <> 'NONE' THEN 1 ELSE 0 END) AS is_foreign,
                 oo.order_amount
          FROM ocmp.moss.order_option oo
          JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
          WHERE om.dummy_order = 0 AND om.order_status = 50 """ + ordf + r"""
        ),
        gmbrand AS (   -- 상품마스터 브랜드(fetch_sales 우선순위와 동일). 주문에 등장한 goods만.
          SELECT CAST(g.goods_no AS BIGINT) AS goods_no, ANY_VALUE(b2.brand_nm) AS gm_brand_nm
          FROM musinsa.bizest.goods g
          LEFT JOIN musinsa.partnerportal.brand b2 ON b2.brand = g.brand
          WHERE CAST(g.goods_no AS BIGINT) IN (SELECT DISTINCT CAST(goods_no AS BIGINT) FROM ord)
          GROUP BY g.goods_no
        )
        SELECT order_id, sales_date, store_name, shop_type, is_foreign,
               business_type, brand_nm, cat_top, cat_large, cat_medium, hour,
               CAST(SUM(order_amount) AS DOUBLE) AS gmv
        FROM (
          SELECT o.order_id, o.sales_date, o.hour, st.store_name, st.shop_type, o.is_foreign,
                 COALESCE(br.business_type, '기타') AS business_type,
                 COALESCE(NULLIF(gm.gm_brand_nm, ''), br.brand_nm, '(미매칭)') AS brand_nm,
                 COALESCE(cat.cat_top, '미분류')    AS cat_top,
                 COALESCE(cat.cat_large, '미분류')  AS cat_large,
                 COALESCE(cat.cat_medium, '미분류') AS cat_medium,
                 o.order_amount
          FROM ord o
          JOIN dim_store st ON st.shop_no = o.shop_no
          LEFT JOIN dim_brand br ON br.com_id = o.company_id AND br.brand_code = o.brand_id
          LEFT JOIN catmap   cat ON cat.goods_no = o.goods_no
          LEFT JOIN gmbrand  gm  ON gm.goods_no = CAST(o.goods_no AS BIGINT)
        ) t
        GROUP BY order_id, sales_date, store_name, shop_type, is_foreign,
                 business_type, brand_nm, cat_top, cat_large, cat_medium, hour
    """
    df = run_df(q)
    df["order_id"] = df["order_id"].astype(str)
    df["sales_date"] = pd.to_datetime(df["sales_date"])
    df["hour"] = pd.to_numeric(df["hour"], errors="coerce").fillna(-1).astype("int64")  # 거래시각 시(0~23, 불명 -1)
    df["is_foreign"] = pd.to_numeric(df["is_foreign"]).fillna(0).astype("int64")
    df["gmv"] = pd.to_numeric(df["gmv"]).fillna(0.0)
    df["business_type"] = df["business_type"].fillna("기타")
    df["brand_nm"] = df["brand_nm"].fillna("(미매칭)")
    for c in ("cat_top", "cat_large", "cat_medium"):
        df[c] = df[c].fillna("미분류")
    return df


@st.cache_data(ttl=86400, persist="disk", show_spinner="판매 데이터 로딩 중... (최초 1회만)")
def load_sales() -> pd.DataFrame:
    """전체 판매 fact (직접 경로 / 하위호환). 평소엔 store.get_sales(DuckDB)를 사용."""
    return fetch_sales(None)


@st.cache_data(ttl=1800, show_spinner=False)
def load_concept_map() -> dict:
    """(company_id, brand_id) → 컨셉값. summary_v 뷰가 업스트림 오류일 수 있어 단독 쿼리 + 예외처리(실패 시 빈 맵).
    persist 없이 짧은 TTL → 뷰 복구 시 자동 반영 (load_sales 등 무거운 캐시에 굽지 않음)."""
    try:
        d = run_df("SELECT company_id, brand_id, ANY_VALUE(concept) AS concept "
                   "FROM team.sales.dsh_d_upt_editorial_summary_v "
                   "WHERE concept IS NOT NULL AND concept <> '' GROUP BY company_id, brand_id")
        return {(str(c), str(b)): v for c, b, v in zip(d["company_id"], d["brand_id"], d["concept"])}
    except Exception:
        return {}


@st.cache_data(ttl=86400, persist="disk", show_spinner=False)
def load_goods_master() -> pd.DataFrame:
    """상품 마스터: goods_no → goods_nm(상품명), brand_nm(브랜드명). 메인 소스 = musinsa.bizest.goods.
    ⚠️ 오프라인에서 주문된 적 있는 goods_no만 적재(전 상품 600만 → 약 11만). sales/inventory/cmptab/
       prodmeta가 참조하는 건 '오프라인에서 팔린 상품'뿐이라(판매 행의 goods_no는 100% 이 집합에 포함)
       enrichment 결과는 동일하면서 메모리/스토리지를 ~52배 줄인다 → 작은 인스턴스 갱신(scale-to-zero) 가능.
       (검증 2026-06-28: 최근 30일 판매 goods 29,803개 누락 0, 표본 속성 불일치 0)
    brand_nm은 brand코드→partnerportal.brand 조인."""
    q = "WITH " + DIM_STORE + r""",
        sold AS (   -- 오프라인 매장에서 주문된 적 있는 goods (환불 goods 포함 위해 order_status 미필터, dummy 제외)
          SELECT DISTINCT CAST(oo.goods_no AS BIGINT) AS goods_no
          FROM ocmp.moss.order_option oo
          JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
          JOIN dim_store st ON st.shop_no = om.shop_no
          WHERE om.dummy_order = 0
        ),
        px AS (   -- 온라인 1차세일가(현재가) = 상품별 최신 1건 (channel_id=1). goods-master 스킬과 동일 정의.
          SELECT CAST(goods_id AS BIGINT) AS goods_no, sale_price AS online_sale
          FROM musinsa.itgg.goods_sale_price_changes
          WHERE channel_id = 1
          QUALIFY row_number() OVER (PARTITION BY goods_id ORDER BY created_at DESC) = 1
        )
        SELECT g.goods_no,
               ANY_VALUE(g.goods_nm) AS goods_nm,
               ANY_VALUE(b.brand_nm) AS brand_nm,
               MIN(CAST(g.reg_dm AS DATE)) AS reg_date,
               ANY_VALUE(g.style_no) AS style_no,
               CAST(ANY_VALUE(g.normal_price) AS DOUBLE) AS normal_price,
               -- 판매가 = 온라인 1차세일가(goods_sale_price_changes), 이력 없으면 bizest price→정상가 폴백
               CAST(COALESCE(ANY_VALUE(px.online_sale), ANY_VALUE(g.price), ANY_VALUE(g.normal_price)) AS DOUBLE) AS sale_price
        FROM musinsa.bizest.goods g
        JOIN sold sg ON CAST(g.goods_no AS BIGINT) = sg.goods_no
        LEFT JOIN musinsa.partnerportal.brand b ON b.brand = g.brand
        LEFT JOIN px ON px.goods_no = CAST(g.goods_no AS BIGINT)
        GROUP BY g.goods_no
    """
    d = run_df(q)
    d["goods_no"] = pd.to_numeric(d["goods_no"]).astype("int64")
    d["goods_nm"] = d["goods_nm"].fillna("")
    d["brand_nm"] = d["brand_nm"].fillna("")
    d["reg_date"] = pd.to_datetime(d["reg_date"], errors="coerce")  # 상품 등록일(reg_dm) — 신규 판정 기준
    d["style_no"] = d["style_no"].fillna("")
    d["normal_price"] = pd.to_numeric(d["normal_price"], errors="coerce").fillna(0.0)   # 현재 정상가
    d["sale_price"] = pd.to_numeric(d["sale_price"], errors="coerce").fillna(0.0)       # 판매가 = 온라인 1차세일가(폴백 bizest→정상가)
    return d


def fetch_targets() -> pd.DataFrame:
    """매장별 일(日) 목표 — gspread.sales.target_retail_editorialshop.
       dt(YYYYMMDD)→sales_date, shop_no(비제스트)→dim_store 매핑되는 오프라인 매장만 INNER JOIN(미매핑 shop 제외).
       gmv_goal=일 GMV 목표, cp_goal=기여이익 목표. 실적(sales.gmv)과 store_name·sales_date로 조인해 목표 대비 실적 산출."""
    q = ("WITH " + DIM_STORE + r"""
        SELECT to_date(CAST(t.dt AS STRING), 'yyyyMMdd')      AS sales_date,
               CAST(t.shop_no AS INT)                          AS shop_no,
               ds.store_name, ds.shop_type,
               CAST(t.gmv_goal AS DOUBLE)                      AS gmv_goal,
               CAST(t.contribution_profit_goal AS DOUBLE)      AS cp_goal
        FROM gspread.sales.target_retail_editorialshop t
        JOIN dim_store ds ON ds.shop_no = CAST(t.shop_no AS INT)
        WHERE t.shop_no IS NOT NULL
    """)
    d = run_df(q)
    d["sales_date"] = pd.to_datetime(d["sales_date"], errors="coerce")
    d["gmv_goal"] = pd.to_numeric(d["gmv_goal"], errors="coerce").fillna(0.0)
    d["cp_goal"] = pd.to_numeric(d["cp_goal"], errors="coerce").fillna(0.0)
    return d[d["sales_date"].notna()]


def fetch_footfall() -> pd.DataFrame:
    """매장 입객수(footfall) 일별 — team.sales.retail_editorialshop_visitors_v (오프라인 매장만).
       그레인: 일자×매장. 구매전환율(=구매건수/입객)에 사용. ⚠️ 온라인 트래픽과 무관."""
    q = ("WITH " + DIM_STORE + r"""
        SELECT to_date(v.dt, 'yyyyMMdd') AS sales_date, ds.store_name, ds.shop_type,
               CAST(SUM(v.visitors_int) AS BIGINT) AS visitors
        FROM team.sales.retail_editorialshop_visitors_v v
        JOIN dim_store ds ON ds.shop_no = v.shop_no
        WHERE v.dt IS NOT NULL
        GROUP BY 1, 2, 3
    """)
    d = run_df(q)
    d["sales_date"] = pd.to_datetime(d["sales_date"], errors="coerce")
    d["visitors"] = pd.to_numeric(d["visitors"], errors="coerce").fillna(0).astype("int64")
    return d[d["sales_date"].notna()]


def fetch_global_customer() -> pd.DataFrame:
    """글로벌 고객 국가별 GMV(면세환급 국적 기준) — ocmp.moss.tax_free_customer.
       그레인: 일자×매장×국적. gmv=gross(환불 미반영, 국가별 환불귀속 불가), buyers=주문수(distinct order_id).
       모집단: fetch_sales와 동일(dummy=0, status=50, 오프라인). 커버리지 ≈ 대시보드 외국인의 ~85%."""
    q = ("WITH " + DIM_STORE + r""",
        tf AS (SELECT DISTINCT order_id, nationality FROM ocmp.moss.tax_free_customer WHERE nationality IS NOT NULL)
        SELECT CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) AS sales_date,
               ds.store_name, ds.shop_type, tf.nationality,
               CAST(SUM(oo.order_amount) AS DOUBLE) AS gmv,
               CAST(COUNT(DISTINCT om.order_id) AS BIGINT) AS buyers
        FROM tf
        JOIN ocmp.moss.order_master om ON CAST(om.order_id AS STRING) = tf.order_id
        JOIN dim_store ds ON ds.shop_no = om.shop_no
        JOIN ocmp.moss.order_option oo ON oo.order_id = om.order_id
        WHERE om.dummy_order = 0 AND om.order_status = 50
        GROUP BY 1, 2, 3, 4
    """)
    d = run_df(q)
    d["sales_date"] = pd.to_datetime(d["sales_date"], errors="coerce")
    d["gmv"] = pd.to_numeric(d["gmv"], errors="coerce").fillna(0.0)
    d["buyers"] = pd.to_numeric(d["buyers"], errors="coerce").fillna(0).astype("int64")
    return d[d["sales_date"].notna()]


def fetch_settlement() -> pd.DataFrame:
    """오프라인 편집샵 순이익(Net Take)·공헌이익(CP) — team.sales.dsh_d_upt_editorial_summary_v.
       그레인: 일자×매장×상품(goods_no). Net Take=profit(순이익), CP=contribution_profit_pre(공헌이익).
       검증 항등식(2026-06-21): CP ≈ profit − offline_cost_fixed + additional_rev.
       ⚠️ CP는 매장 고정비(offline_cost_fixed) 배부값이라 최근 ~2개월은 예측치(변동)·상품 1건당 음수 가능.
       대시보드 매장(dim_store)만. GMV(MOSS 기반)와 별개 지표로 추가."""
    q = ("WITH " + DIM_STORE + r"""
        SELECT v.ord_state_date AS sales_date, ds.store_name, ds.shop_type,
               CAST(v.goods_no AS BIGINT) AS goods_no,
               CAST(SUM(v.profit) AS DOUBLE) AS net_take,
               CAST(SUM(v.contribution_profit_pre) AS DOUBLE) AS cp
        FROM team.sales.dsh_d_upt_editorial_summary_v v
        JOIN dim_store ds ON ds.shop_no = v.shop_no
        WHERE v.ord_state_date IS NOT NULL AND v.goods_no IS NOT NULL
        GROUP BY 1, 2, 3, 4
    """)
    d = run_df(q)
    d["sales_date"] = pd.to_datetime(d["sales_date"], errors="coerce")
    d["goods_no"] = pd.to_numeric(d["goods_no"], errors="coerce").fillna(0).astype("int64")
    d["net_take"] = pd.to_numeric(d["net_take"], errors="coerce").fillna(0.0)
    d["cp"] = pd.to_numeric(d["cp"], errors="coerce").fillna(0.0)
    return d[d["sales_date"].notna()]


@st.cache_data(ttl=3600, show_spinner=False)
def load_md_names() -> dict:
    """offline_md_id(예: minsu.kim) → 한글명. 여러 소스의 (md_id→md_nm)를 합쳐 최대 커버리지."""
    q = r"""
        WITH l AS (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary)
        SELECT md_id, md_nm FROM team.sales.dsh_d_upt_editorial_stock_summary s JOIN l ON s.ord_state_date=l.d
          WHERE md_id IS NOT NULL AND md_nm IS NOT NULL
        UNION ALL SELECT md_id, md_nm FROM musinsa.partnerportal.company WHERE md_id IS NOT NULL AND md_nm IS NOT NULL
        UNION ALL SELECT md_id, md_nm FROM musinsa.bizest.goods WHERE md_id IS NOT NULL AND md_nm IS NOT NULL
    """
    d = run_df(q)
    d = d[d["md_nm"].astype(str).str.strip() != ""]
    # md_id별 최빈 한글명
    return (d.groupby("md_id")["md_nm"].agg(lambda s: s.value_counts().index[0]).to_dict())


# ---------------------------------------------------------------------------
# 재고 fact (editorial_stock_summary 최신 스냅샷) — sales와 동일 shop_no 기준
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="재고 데이터 로딩 중... (최초 1회만)")
def load_inventory() -> pd.DataFrame:
    q = (
        "WITH " + DIM_STORE + r""",
        l AS (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary)
        SELECT
          CASE s.ord_com_type WHEN '입점(위탁)' THEN '위탁' WHEN '공급(매입)' THEN '매입' ELSE '기타' END AS business_type,
          st.store_name, st.shop_type, s.brand_nm,
          CAST(s.goods_no AS BIGINT) AS goods_no,
          ANY_VALUE(s.goods_nm) AS goods_nm,
          MAX(l.d) AS snapshot_date,
          CAST(SUM(s.sellable_qty)  AS DOUBLE) AS sellable_qty,
          CAST(SUM(s.available_qty) AS DOUBLE) AS available_qty
        FROM team.sales.dsh_d_upt_editorial_stock_summary s
        JOIN l ON s.ord_state_date = l.d
        JOIN dim_store st ON st.shop_no = s.shop_no
        GROUP BY 1,2,3,4,5
        """
    )
    df = run_df(q)
    df["goods_no"] = pd.to_numeric(df["goods_no"]).astype("int64")
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    for c in ("sellable_qty", "available_qty"):
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    df["brand_nm"] = df["brand_nm"].fillna("(미매칭)")
    return df


# ---------------------------------------------------------------------------
# 고객/외국인 (dsh_d_upt_editorial_summary_customer) — 면세(tax)=외국인, 성별/연령/회원
# ---------------------------------------------------------------------------
@st.cache_data(ttl=86400, persist="disk", show_spinner="고객 데이터 로딩 중... (최초 1회만)")
def load_customer() -> pd.DataFrame:
    q = (
        "WITH " + DIM_STORE + r"""
        SELECT t.ord_state_date AS sales_date, st.store_name, st.shop_type,
               COALESCE(NULLIF(t.sex,''),'기타')              AS sex,
               COALESCE(NULLIF(t.age_band_custom,''),'기타')  AS age_band,
               COALESCE(NULLIF(t.mssid_yn,''),'미상')         AS member,
               CAST(SUM(t.gmv)        AS DOUBLE) AS gmv,
               CAST(SUM(t.normal_gmv) AS DOUBLE) AS normal_gmv,
               CAST(SUM(t.tax_gmv)    AS DOUBLE) AS foreign_gmv,
               CAST(SUM(t.qty)        AS DOUBLE) AS qty,
               CAST(SUM(t.buyer)      AS DOUBLE) AS buyer,
               CAST(SUM(t.tax_buyer)  AS DOUBLE) AS foreign_buyer
        FROM team.sales.dsh_d_upt_editorial_summary_customer t
        JOIN dim_store st ON st.shop_no = t.shop_no
        WHERE t.summary_period_unit = 'daily_total'
        GROUP BY 1,2,3,4,5,6
        """
    )
    df = run_df(q)
    df["sales_date"] = pd.to_datetime(df["sales_date"])
    for c in ("gmv", "normal_gmv", "foreign_gmv", "qty", "buyer", "foreign_buyer"):
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    return df


# ---------------------------------------------------------------------------
# 재고 피벗 — 상품·옵션(barcode) × 매장 + 창고.
# 정의(전산 일치): 위탁=SCM-HUB(=MFS) / 매입=플랜트1000(창고 2000·2010·2020·2060)+플랜트1700(2000)+오프라인 매장재고.
# 허브1000은 plant1000의 지정 창고만(LIKE '20%'는 신규 2011~2019/2040/2071 등을 끌어와 과대계상 → IN 목록으로 고정).
# ---------------------------------------------------------------------------
HUB_COLS = ["MFS", "허브1000", "허브1700"]


@st.cache_data(ttl=86400, persist="disk", show_spinner="재고 피벗 로딩 중... (최초 1회만)")
def load_inventory_pivot() -> pd.DataFrame:
    store_long = run_df(
        "WITH " + DIM_STORE + r""",
        l AS (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary)
        SELECT s.barcode,
               ANY_VALUE(s.goods_no)  AS goods_no,
               ANY_VALUE(s.goods_opt) AS goods_opt,
               ANY_VALUE(s.brand_nm)  AS brand_nm,
               ANY_VALUE(s.goods_nm)  AS goods_nm,
               ANY_VALUE(s.final_large_nm_off) AS cat_top,
               ANY_VALUE(s.large_nm_off)       AS cat_large,
               ANY_VALUE(s.medium_nm_off)      AS cat_medium,
               ANY_VALUE(s.offline_md_id)      AS off_md_id,
               ANY_VALUE(s.com_id)             AS company_id,
               ANY_VALUE(s.brand)              AS brand_id,
               ANY_VALUE(CASE s.ord_com_type WHEN '입점(위탁)' THEN '위탁' WHEN '공급(매입)' THEN '매입' ELSE '기타' END) AS business_type,
               st.store_name,
               CAST(SUM(s.sellable_qty) AS DOUBLE) AS qty
        FROM team.sales.dsh_d_upt_editorial_stock_summary s
        JOIN l ON s.ord_state_date = l.d
        JOIN dim_store st ON st.shop_no = s.shop_no
        WHERE s.barcode IS NOT NULL
        GROUP BY s.barcode, st.store_name
        """
    )
    # 플랜트1700(트레이딩) 매장 재고 — sap_inventory → shop_location(plant,location→비제스트 shop_no) → dim_store
    store1700 = run_df(
        "WITH " + DIM_STORE + r"""
        SELECT sap.barcode, ds.store_name,
               ANY_VALUE(sap.goods_no)    AS goods_no,
               ANY_VALUE(sap.option_name) AS goods_opt,
               CAST(NULL AS STRING) AS brand_nm, CAST(NULL AS STRING) AS goods_nm,
               CAST(NULL AS STRING) AS cat_top, CAST(NULL AS STRING) AS cat_large, CAST(NULL AS STRING) AS cat_medium,
               CAST(NULL AS STRING) AS off_md_id,
               CAST(NULL AS STRING) AS company_id, CAST(NULL AS STRING) AS brand_id,
               '매입' AS business_type,
               CAST(SUM(sap.available_stock) AS DOUBLE) AS qty
        FROM ocmp.moss.sap_inventory sap
        JOIN ocmp.moss.shop_location sl
          ON sl.erp_plant_code = sap.plant_code AND sl.erp_location_code = sap.storage_location
        JOIN dim_store ds ON ds.shop_no = CAST(sl.shop_no AS INT)
        WHERE sap.plant_code = '1700' AND sap.barcode IS NOT NULL
        GROUP BY sap.barcode, ds.store_name
        """
    )
    store_long = pd.concat([store_long, store1700], ignore_index=True)
    hub_long = run_df(
        "WITH " + DIM_STORE + r""",
        l AS (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary),
        bc AS (SELECT DISTINCT s.barcode FROM team.sales.dsh_d_upt_editorial_stock_summary s
               JOIN l ON s.ord_state_date = l.d JOIN dim_store st ON st.shop_no = s.shop_no
               WHERE s.barcode IS NOT NULL),
        le AS (SELECT MAX(dt) d FROM team.partner.raw_erp_stock),
        erp1700 AS (SELECT DISTINCT barcode FROM team.partner.raw_erp_stock r JOIN le ON r.dt = le.d
                    WHERE r.store_cd='1700' AND r.lgort='2000' AND r.barcode IS NOT NULL),
        hubmap AS (
          SELECT sku_id, supplier_barcode FROM (
            SELECT sku_id, supplier_barcode,
                   ROW_NUMBER() OVER (PARTITION BY sku_id ORDER BY strd_dt DESC) rn
            FROM team.scm.scm_hub_goods_meta WHERE supplier_barcode IS NOT NULL) WHERE rn = 1)
        SELECT r.barcode AS barcode, '허브1000' AS hub, CAST(SUM(r.wqty) AS DOUBLE) AS qty
          FROM team.partner.raw_erp_stock r JOIN le ON r.dt = le.d
          WHERE r.store_cd='1000' AND r.lgort IN ('2000','2010','2020','2060') AND r.barcode IN (SELECT barcode FROM bc)
          GROUP BY r.barcode
        UNION ALL
        SELECT r.barcode, '허브1700', CAST(SUM(r.wqty) AS DOUBLE)
          FROM team.partner.raw_erp_stock r JOIN le ON r.dt = le.d
          WHERE r.store_cd='1700' AND r.lgort='2000' AND r.barcode IS NOT NULL
          GROUP BY r.barcode
        UNION ALL
        SELECT h.supplier_barcode, 'MFS', CAST(SUM(f.mfs_stock_qty) AS DOUBLE)
          FROM team.commercepm.mfs_stock_daily f JOIN hubmap h ON h.sku_id = f.sku_id
          WHERE h.supplier_barcode IN (SELECT barcode FROM bc UNION SELECT barcode FROM erp1700)
          GROUP BY h.supplier_barcode
    """)

    hub_meta = run_df(r"""
        WITH le AS (SELECT MAX(dt) d FROM team.partner.raw_erp_stock),
        hubbc AS (SELECT DISTINCT barcode FROM team.partner.raw_erp_stock r JOIN le ON r.dt = le.d
                  WHERE r.store_cd='1700' AND r.lgort='2000' AND r.barcode IS NOT NULL),
        m AS (SELECT supplier_barcode AS bc, brand_name, product_no, product_name, option_name,
                     ROW_NUMBER() OVER (PARTITION BY supplier_barcode ORDER BY strd_dt DESC) rn
              FROM team.scm.scm_hub_goods_meta WHERE supplier_barcode IS NOT NULL)
        SELECT m.bc AS barcode, ANY_VALUE(m.brand_name) AS brand_nm, ANY_VALUE(m.product_no) AS goods_no,
               ANY_VALUE(m.product_name) AS goods_nm, ANY_VALUE(m.option_name) AS goods_opt
        FROM m WHERE m.rn = 1 AND m.bc IN (SELECT barcode FROM hubbc)
        GROUP BY m.bc
    """)

    meta_cols = ["goods_no", "goods_opt", "brand_nm", "goods_nm",
                 "cat_top", "cat_large", "cat_medium", "off_md_id",
                 "company_id", "brand_id", "business_type"]
    store_meta = store_long.groupby("barcode")[meta_cols].first()
    store_piv = store_long.pivot_table(index="barcode", columns="store_name", values="qty",
                                        aggfunc="sum", fill_value=0)
    store_cols = list(store_piv.columns)
    hub_piv = hub_long.pivot_table(index="barcode", columns="hub", values="qty",
                                   aggfunc="sum", fill_value=0)

    # 행 기준 = 매장 ∪ 허브1700 ∪ MFS barcode (허브1000 단독 매입창고 항목은 행 제외, 값은 유지)
    spine_hub = hub_long.loc[hub_long["hub"] == "허브1700", "barcode"].unique()
    all_bc = store_piv.index.union(pd.Index(spine_hub, name="barcode"))
    store_piv = store_piv.reindex(all_bc, fill_value=0)
    hub_piv = hub_piv.reindex(all_bc)
    for hc in HUB_COLS:
        if hc not in hub_piv:
            hub_piv[hc] = 0.0
    hub_piv = hub_piv[HUB_COLS].fillna(0.0)

    # 메타: 매장(editorial) 우선, 없으면 창고(scm) 보강
    hm = hub_meta.set_index("barcode")
    meta = store_meta.reindex(all_bc)
    for col in ["goods_no", "goods_opt", "brand_nm", "goods_nm"]:
        if col in hm.columns:
            meta[col] = meta[col].fillna(hm[col].reindex(all_bc))
    meta["business_type"] = meta["business_type"].fillna("매입")
    for col in ("cat_top", "cat_large", "cat_medium"):
        meta[col] = meta[col].fillna("미분류")
    meta["off_md_id"] = meta["off_md_id"].fillna("").astype(str)

    df = meta.join(store_piv).join(hub_piv)
    df["점재고합계"] = df[store_cols].sum(axis=1)
    df["허브합계"] = df[HUB_COLS].sum(axis=1)

    df = df.reset_index().rename(columns={"index": "barcode"})
    if "barcode" not in df.columns:
        df = df.rename(columns={df.columns[0]: "barcode"})
    df["goods_no"] = pd.to_numeric(df["goods_no"], errors="coerce").fillna(0).astype("int64")
    # 상품명·브랜드명 메인 소스 = 상품마스터(bizest.goods), 없으면 기존(editorial/scm)
    # ⚠️ 전체 load_goods_master(600만×다컬럼)는 st.cache pickle 시 OOM → 이름 3컬럼만 슬림 조회(캐시 없음, arrow).
    gm = run_df(r"""
        SELECT g.goods_no, ANY_VALUE(g.goods_nm) AS goods_nm, ANY_VALUE(b.brand_nm) AS brand_nm
        FROM musinsa.bizest.goods g
        LEFT JOIN musinsa.partnerportal.brand b ON b.brand = g.brand
        GROUP BY g.goods_no
    """)
    gm["goods_no"] = pd.to_numeric(gm["goods_no"], errors="coerce").fillna(0).astype("int64")
    gm["goods_nm"] = gm["goods_nm"].fillna("")
    gm["brand_nm"] = gm["brand_nm"].fillna("")
    gm = gm.set_index("goods_no")
    mnm = df["goods_no"].map(gm["goods_nm"]).replace("", None)
    mbr = df["goods_no"].map(gm["brand_nm"]).replace("", None)
    df["goods_nm"] = mnm.fillna(df["goods_nm"]).fillna("")
    df["brand_nm"] = mbr.fillna(df["brand_nm"]).fillna("(미상)")
    df["goods_opt"] = df["goods_opt"].fillna("")
    df["company_id"] = df["company_id"].fillna("").astype(str)
    df["brand_id"] = df["brand_id"].fillna("").astype(str)
    _cm = load_concept_map()
    df["concept"] = [_cm.get((c, b), "미지정") for c, b in zip(df["company_id"], df["brand_id"])]
    qty_cols = store_cols + ["점재고합계"] + HUB_COLS + ["허브합계"]
    for c in qty_cols:
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    df.attrs["store_cols"] = store_cols
    # 재고 스냅샷 기준일(editorial ord_state_date 최신) — 데이터 기준일자 표기용
    try:
        df.attrs["data_date"] = str(run_df(
            "SELECT CAST(MAX(ord_state_date) AS STRING) d FROM team.sales.dsh_d_upt_editorial_stock_summary"
        ).iloc[0, 0])[:10]
    except Exception:
        pass
    return df


@st.cache_data(ttl=86400, persist="disk", show_spinner=False)
def load_inventory_goods() -> pd.DataFrame:
    """재고 피벗을 상품(goods_no) 단위로 합산 — 판매 상세에 매장별/허브 재고를 붙이기 위함."""
    inv = load_inventory_pivot()
    meta = {"barcode", "goods_no", "goods_opt", "brand_nm", "goods_nm", "business_type",
            "cat_top", "cat_large", "cat_medium", "off_md_id", "concept",
            "company_id", "brand_id", "점재고합계", "허브합계", *HUB_COLS}
    store_cols = [c for c in inv.columns if c not in meta]
    cols = store_cols + ["점재고합계", "허브합계"] + HUB_COLS
    num = inv.groupby("goods_no")[cols].sum()
    nm = inv.groupby("goods_no")["goods_nm"].first()
    return num.join(nm).reset_index()


@st.cache_data(ttl=86400, persist="disk", show_spinner=False)
def load_inventory_store_long() -> pd.DataFrame:
    """(goods_no, store_name, 점재고) 롱포맷 — 요약 탭 재고보충 조인용 (1회 계산·캐시)."""
    g = load_inventory_goods()
    store_cols = [c for c in g.columns
                  if c not in ({"goods_no", "goods_nm", "점재고합계", "허브합계"} | set(HUB_COLS))]
    return g.melt(id_vars="goods_no", value_vars=store_cols, var_name="store_name", value_name="점재고")
