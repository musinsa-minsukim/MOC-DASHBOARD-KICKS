"""Databricks 접속 + 판매/재고 데이터 1회 로딩.

성능 전략: 무거운 통합 쿼리는 세션당 1번만 실행해 집계된 DataFrame을 캐시.
이후 필터/집계/신장율은 전부 pandas에서 즉시 처리.
"""
from __future__ import annotations

import os
import re
import datetime as _dt

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
    #   .strip(): 시크릿/env에 끝 개행·공백이 섞이면(예: GitHub Secret·echo 저장 시 \n) access token이
    #   'dapi...\n'이 돼 Databricks가 'Invalid access token'으로 거부 → 사전 제거로 방지.
    host = (os.environ.get("DATABRICKS_HOST") or "").strip() or None
    http_path = (os.environ.get("DATABRICKS_HTTP_PATH") or "").strip() or None
    token = (os.environ.get("DATABRICKS_TOKEN") or "").strip() or None
    if not (host and http_path and token):
        c = st.secrets["databricks"]
        host, http_path, token = c["host"], c["http_path"], c["token"]
    # use_cloud_fetch: 대용량 결과 수신 방식. True=CDN(Arrow 벌크 다운로드, 훨씬 빠름) / False=SQL 엔드포인트 직수신(느림).
    #   ⚠️ 회사 프록시(SASE) truststore가 CloudFetch 다운로드 TLS(verify_mode/check_hostname)와 충돌해
    #      로컬에선 간헐 'Cannot set verify_mode to CERT_NONE' 오류 → 기본 False(안전, 로컬/Cloud Run 불변).
    #   ✅ 프록시 없는 환경(GitHub Actions 러너)에선 DBX_CLOUD_FETCH=1 로 켜서 대용량 페치 가속
    #      (sales 45일·ips_goods 11만행 등이 Thrift 행단위 수신으로 15분 timeout 걸리던 문제 해소).
    cloud_fetch = os.environ.get("DBX_CLOUD_FETCH", "0") == "1"
    return sql.connect(server_hostname=host, http_path=http_path, access_token=token,
                       use_cloud_fetch=cloud_fetch)


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
# 카테고리 오탐 보정(상품 카탈로그 원천 오류 대응) — 캐시 생성 시점에 일괄 적용.
#   규칙: 크록스 지비츠류(브랜드=크록스 AND 정상가 0~25,900)가 Shoes로 잘못 분류됨 → Acc/기타/기타.
#   (지비츠·액세서리는 5,000~25,900원, 진짜 크록스 신발은 최저 39,900원이라 25,900 상한에 안 걸림.)
#   goods_no 기준 집합을 원천에서 산출 → sales/sales_option/inventory 모두 동일하게 반영.
# ---------------------------------------------------------------------------
_CAT_OVERRIDE_ACC = ("Acc", "기타", "기타")   # (cat_top, cat_large, cat_medium)
_CAT_OVERRIDE_ETC = ("기타", "기타", "기타")   # 슈케어 브랜드 전상품 보정 타깃

# 슈케어 브랜드(신발 아님) — 원천에서 전상품이 Shoes로 오분류됨 → 전상품 기타로 보정(가격 무관).
#   brand_nm LIKE 매칭(한글 표기 + 영문 병기). 신규 상품도 자동 반영.
_SHOECARE_LIKE = ("%프리덕%", "%사피르%", "%콜로닐%", "%SAPHIR%", "%COLLONIL%")


def _shoecare_like_sql(col: str) -> str:
    """col(brand_nm)이 슈케어 브랜드와 매칭되는 LIKE 조건(upper 대소문자 무시)."""
    return "(" + " OR ".join(f"upper(COALESCE({col},'')) LIKE upper('{p}')" for p in _SHOECARE_LIKE) + ")"


@st.cache_data(ttl=3600, show_spinner=False)
def _shoecare_etc_goods() -> frozenset:
    """Shoes로 오분류되는 슈케어 브랜드(프리덕·사피르·콜로닐) 전체 goods_no → 기타. 가격 조건 없음.
       bizest(브랜드마스터) ∪ editorial(오프라인 판매/재고 최신 스냅샷) 양쪽에서 수집."""
    q = f"""
      WITH bz AS (
        SELECT CAST(g.goods_no AS BIGINT) AS gno
        FROM musinsa.bizest.goods g
        LEFT JOIN musinsa.partnerportal.brand b ON b.brand = g.brand
        WHERE {_shoecare_like_sql('b.brand_nm')}
      ),
      l AS (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary),
      ed AS (
        SELECT DISTINCT CAST(s.goods_no AS BIGINT) AS gno
        FROM team.sales.dsh_d_upt_editorial_stock_summary s JOIN l ON s.ord_state_date = l.d
        WHERE {_shoecare_like_sql('s.brand_nm')}
      )
      SELECT DISTINCT gno AS goods_no FROM (SELECT gno FROM bz UNION SELECT gno FROM ed)
    """
    try:
        return frozenset(int(x) for x in run_df(q)["goods_no"].tolist())
    except Exception as e:
        import logging as _lg
        _lg.warning("슈케어 카테고리 보정 goods 조회 실패(보정 생략): %s", e)
        return frozenset()


@st.cache_data(ttl=3600, show_spinner=False)
def _crocs_acc_goods() -> frozenset:
    """카테고리를 Acc로 보정할 goods_no 집합 = 크록스(bizest∪editorial 브랜드) AND 정상가 0~25,900."""
    q = r"""
      WITH bz AS (
        SELECT CAST(g.goods_no AS BIGINT) AS gno
        FROM musinsa.bizest.goods g
        LEFT JOIN musinsa.partnerportal.brand b ON b.brand = g.brand
        WHERE upper(COALESCE(b.brand_nm,'')) LIKE '%CROCS%' OR COALESCE(b.brand_nm,'') LIKE '%크록스%'
      ),
      l AS (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary),
      ed AS (
        SELECT DISTINCT CAST(s.goods_no AS BIGINT) AS gno
        FROM team.sales.dsh_d_upt_editorial_stock_summary s JOIN l ON s.ord_state_date = l.d
        WHERE upper(COALESCE(s.brand_nm,'')) LIKE '%CROCS%' OR COALESCE(s.brand_nm,'') LIKE '%크록스%'
      ),
      allc AS (SELECT gno FROM bz UNION SELECT gno FROM ed)
      SELECT DISTINCT a.gno AS goods_no
      FROM allc a JOIN musinsa.bizest.goods g ON CAST(g.goods_no AS BIGINT) = a.gno
      WHERE g.normal_price BETWEEN 0 AND 25900
    """
    try:
        return frozenset(int(x) for x in run_df(q)["goods_no"].tolist())
    except Exception as e:
        import logging as _lg
        _lg.warning("crocs 카테고리 보정 goods 조회 실패(보정 생략): %s", e)
        return frozenset()


def apply_category_override(df):
    """goods_no + cat_top/cat_large/cat_medium 를 가진 df에 카테고리 오탐 보정 적용.
       ① 크록스 지비츠(정상가 0~25,900) → Acc/기타/기타
       ② 슈케어 브랜드(프리덕·사피르·콜로닐) 전상품 → 기타/기타/기타 (Shoes 오분류 보정, 가격 무관)."""
    if df is None or "goods_no" not in getattr(df, "columns", []):
        return df
    for ov, cat in ((_crocs_acc_goods(), _CAT_OVERRIDE_ACC),
                    (_shoecare_etc_goods(), _CAT_OVERRIDE_ETC)):
        if not ov:
            continue
        m = df["goods_no"].isin(ov)
        if not m.any():
            continue
        for col, val in zip(("cat_top", "cat_large", "cat_medium"), cat):
            if col in df.columns:
                df.loc[m, col] = val
    return df


# ---------------------------------------------------------------------------
# 러닝화 태그 — '무신사 런(RUN, shop_no=90)' 매장 취급 신발 = 러닝화(고정밀·자동유지).
#   RUN은 러닝 전문점이라 거기 취급된 신발이면 러닝화로 본다(예: 젤-1130 라이프스타일은 RUN 미취급 → 제외).
#   RUN '신발(cat_top=Shoes)' 중 카탈로그 오탐으로 섞인 의류/잡화 이름은 제외. 신규 RUN 입고분은 자동 반영.
# ---------------------------------------------------------------------------
_RUN_APPAREL_RE = re.compile(
    r"티셔츠|티셔트|반팔|긴팔|재킷|자켓|후디|후드|쇼츠|쇼트|팬츠|바지|레깅스|타이츠|양말|삭스|sock|"
    r"모자|비니|헤어\s*밴드|헤어밴드|장갑|가방|백팩|\bbag\b|벨트|탱크|집업|조끼|베스트|스카프|반다나|"
    r"우산|보틀|물통|파우치|암워머|넥워머|셔츠|슬리브|팬티|드로즈|싱글렛|캡\b|\b캡|크롭|\b탑\b|"
    r"베이스레이어|스포츠\s*브라|브라\b|헤드\s*밴드|헤드밴드|스커트|우븐|워머", re.I)


@st.cache_data(ttl=3600, show_spinner=False)
def _running_shoe_goods() -> frozenset:
    """러닝화 goods_no 집합 = RUN(shop_no=90) 취급 신발(cat_top=Shoes) − 의류/잡화 이름."""
    q = r"""
      WITH runsold AS (
        SELECT DISTINCT CAST(oo.goods_no AS BIGINT) gno
        FROM ocmp.moss.order_option oo
        JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
        WHERE om.shop_no = 90 AND om.dummy_order = 0
      ),
      l AS (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary),
      runstock AS (
        SELECT DISTINCT CAST(goods_no AS BIGINT) gno
        FROM team.sales.dsh_d_upt_editorial_stock_summary s JOIN l ON s.ord_state_date = l.d
        WHERE s.shop_no = 90
      ),
      runall AS (SELECT gno FROM runsold UNION SELECT gno FROM runstock),
      cat AS (
        SELECT CAST(goods_no AS BIGINT) gno, ANY_VALUE(final_large_nm_off) cat_top
        FROM team.sales.dsh_d_upt_editorial_stock_summary s2 JOIN l ON s2.ord_state_date = l.d GROUP BY 1
      )
      SELECT r.gno AS goods_no, g.goods_nm
      FROM runall r JOIN cat c ON c.gno = r.gno
      LEFT JOIN musinsa.bizest.goods g ON CAST(g.goods_no AS BIGINT) = r.gno
      WHERE c.cat_top = 'Shoes'
    """
    try:
        df = run_df(q)
    except Exception as e:
        import logging as _lg
        _lg.warning("RUN 러닝화 goods 조회 실패(러닝 태그 생략): %s", e)
        return frozenset()
    nm = df["goods_nm"].fillna("").astype(str)
    keep = df[~nm.str.contains(_RUN_APPAREL_RE)]
    return frozenset(int(x) for x in keep["goods_no"].tolist())


def apply_running_flag(df):
    """goods_no 가진 df에 is_running(러닝화=1) 컬럼 부여. (RUN 매장 기반 러닝화 집합 기준)"""
    if df is None or "goods_no" not in getattr(df, "columns", []):
        return df
    rs = _running_shoe_goods()
    df["is_running"] = df["goods_no"].isin(rs).astype("int64") if rs else 0
    return df


# ---------------------------------------------------------------------------
# 컨셉(concept) — team.marketing.offline_page0_dim_concept (goods_no→concept, 1:1).
#   값: 걸즈/영/포멀/킥스/뷰티/잡화/포우먼/기타. 판매·재고 전 탭 필터 및 비중 차원.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _concept_by_goods() -> dict:
    """goods_no → concept. 오프라인 판매 이력 goods로 한정(메모리 절약)."""
    try:
        d = run_df(r"""
          WITH sold AS (
            SELECT DISTINCT CAST(oo.goods_no AS BIGINT) gno
            FROM ocmp.moss.order_option oo JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
            WHERE om.dummy_order = 0
          )
          SELECT CAST(c.goods_no AS BIGINT) AS goods_no, c.concept
          FROM team.marketing.offline_page0_dim_concept c
          JOIN sold s ON s.gno = CAST(c.goods_no AS BIGINT)
          WHERE c.concept IS NOT NULL AND c.concept <> ''
        """)
        return {int(g): str(v) for g, v in zip(d["goods_no"], d["concept"])}
    except Exception as e:
        import logging as _lg
        _lg.warning("concept 매핑 조회 실패(concept 생략): %s", e)
        return {}


def apply_concept(df):
    """goods_no 가진 df에 concept 컬럼 부여(마케팅 컨셉 dim). 미매핑은 '미매핑'."""
    if df is None or "goods_no" not in getattr(df, "columns", []):
        return df
    cm = _concept_by_goods()
    df["concept"] = df["goods_no"].map(cm).fillna("미매핑") if cm else "미매핑"
    return df


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
        --   (검증: 2026-06-21 총주문−환불 = 765,922,600원 / 순판매수량 20,649개 로 기존 MOSS 검증값과 정확히 일치.
        --    교환은 수량중립이라 제외. ※ 20,649는 영수증수가 아니라 순판매수량이고, shop_no 19(뷰티 스페이스 1) 제외 기준이다.
        --    그날 영수증수는 9,366건. 19번 포함 시 766,039,700 / 20,658.)
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
    df = apply_category_override(df)   # 크록스 지비츠(정상가 0~25900) Shoes→Acc 등 카탈로그 오탐 보정
    df = apply_running_flag(df)        # is_running: RUN 매장 취급 신발=러닝화
    df = apply_concept(df)             # concept: 마케팅 컨셉 dim(걸즈/영/포멀/킥스/뷰티/잡화/포우먼/기타)
    # 쇼핑백(수베니어샵)은 순판매수량에서 제외 — qty=0 처리(매출 gmv·정상가 등은 유지). 전 지표/탭 일관 반영.
    df.loc[df["brand_nm"] == "수베니어샵", "qty"] = 0.0
    return df  # concept는 app에서 load_concept_map으로 매핑(뷰 불안정 분리)


def fetch_sales_option(since: str | None = None) -> pd.DataFrame:
    """옵션 단위 오프라인 판매(신선 — 오늘 포함) — 판매 CSV(매출일자×매장×상품×옵션) 베이스.
       fetch_sales와 동일 주문원장(order_option+master+claim)·환불귀속, 옵션(option_name) 그레인만 추가.
       goods_nm/option_nm은 원장 값. 순이익/CP는 익일 반영이라 여기 없음(CSV에서 settlement_option 조인).
       → settlement_option이 stale/미반영이어도 CSV가 항상 매출일자×옵션으로 나오게 하는 신선 소스.
       히스토리: since 주어지면 그 이후만(증분 window), None(full 빌드)이면 전이력(하한 없음).
       ⚠️ full 빌드는 옵션단위 전이력이라 무겁다(최초 1회) — 이후 증분(window)은 과거를 보존하며
          최근만 재적재해 가볍다. (하한 제거: 사용자 요청으로 CSV 옵션단위를 전기간 지원)"""
    if since:
        ordf = f"AND CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) >= '{since}'"
        reff = f"AND CAST(created_at AS DATE) >= '{since}'"
    else:
        ordf = ""   # 전이력(하한 없음)
        reff = ""
    q = ("WITH " + DIM_STORE + r""",
        dim_brand AS (
          SELECT cb.com_id, cb.brand AS brand_code, b.brand_nm,
            CASE c.margin_type WHEN 'FEE' THEN '위탁' WHEN 'WONGA' THEN '매입' ELSE '기타' END AS business_type
          FROM musinsa.partnerportal.company_brand cb
          LEFT JOIN musinsa.partnerportal.brand   b ON b.brand  = cb.brand
          LEFT JOIN musinsa.partnerportal.company c ON c.com_id = cb.com_id
        ),
        catmap AS (
          SELECT goods_no, ANY_VALUE(final_large_nm_off) AS cat_top, ANY_VALUE(large_nm_off) AS cat_large,
                 ANY_VALUE(medium_nm_off) AS cat_medium, ANY_VALUE(offline_md_id) AS off_md_id
          FROM team.sales.dsh_d_upt_editorial_stock_summary s2
          JOIN (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary) lc
            ON s2.ord_state_date = lc.d
          GROUP BY goods_no
        ),
        ord AS (
          SELECT CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) AS sales_date,
                 om.shop_no, oo.goods_no, oo.company_id, oo.brand_id, oo.goods_name, oo.option_name, oo.sku_id,
                 (CASE WHEN om.tax_refund_type IS NOT NULL AND om.tax_refund_type <> 'NONE' THEN 1 ELSE 0 END) AS is_foreign,
                 1 AS sgn, oo.quantity, oo.order_amount, oo.normal_amount, oo.pay_amount
          FROM ocmp.moss.order_option oo
          JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
          WHERE om.dummy_order = 0 AND om.order_status = 50 """ + ordf + r"""
        ),
        refclaim AS (
          SELECT order_id, CAST(created_at AS DATE) AS refund_date
          FROM ocmp.moss.claim WHERE claim_type = 'REFUND' """ + reff + r"""
          GROUP BY order_id, CAST(created_at AS DATE)
        ),
        ref AS (
          SELECT rc.refund_date AS sales_date,
                 om.shop_no, oo.goods_no, oo.company_id, oo.brand_id, oo.goods_name, oo.option_name, oo.sku_id,
                 (CASE WHEN om.tax_refund_type IS NOT NULL AND om.tax_refund_type <> 'NONE' THEN 1 ELSE 0 END) AS is_foreign,
                 -1 AS sgn, oo.quantity, oo.order_amount, oo.normal_amount, oo.pay_amount
          FROM refclaim rc
          JOIN ocmp.moss.order_master om ON om.order_id = rc.order_id
          JOIN ocmp.moss.order_option oo ON oo.order_id = rc.order_id
          WHERE om.dummy_order = 0
        ),
        lines AS (SELECT * FROM ord UNION ALL SELECT * FROM ref),
        fact AS (
          SELECT l.sales_date, st.store_name, st.shop_type,
                 COALESCE(br.brand_nm, '(미매칭)') AS brand_nm,
                 COALESCE(br.business_type, '기타') AS business_type,
                 CAST(l.goods_no AS BIGINT) AS goods_no, l.goods_name AS goods_nm,
                 COALESCE(NULLIF(TRIM(l.option_name), ''), '(옵션없음)') AS option_nm, l.sku_id,
                 cat.cat_top, cat.cat_large, cat.cat_medium, cat.off_md_id,
                 l.is_foreign, l.sgn, l.quantity, l.order_amount, l.normal_amount, l.pay_amount
          FROM lines l
          JOIN dim_store st ON st.shop_no = l.shop_no
          LEFT JOIN dim_brand br ON br.com_id = l.company_id AND br.brand_code = l.brand_id
          LEFT JOIN catmap cat ON cat.goods_no = l.goods_no
        )
        SELECT sales_date, store_name, shop_type, business_type, brand_nm,
               CAST(goods_no AS BIGINT) AS goods_no, ANY_VALUE(goods_nm) AS goods_nm, option_nm,
               MAX(CAST(sku_id AS STRING)) AS sku_id,
               cat_top, cat_large, cat_medium, off_md_id,
               CAST(SUM(sgn*quantity)     AS DOUBLE) AS qty,
               CAST(SUM(sgn*order_amount) AS DOUBLE) AS gmv,
               CAST(SUM(sgn*normal_amount) AS DOUBLE) AS normal_amt,
               CAST(SUM(sgn*pay_amount)   AS DOUBLE) AS pay,
               CAST(SUM(sgn*order_amount*is_foreign) AS DOUBLE) AS foreign_gmv
        FROM fact
        GROUP BY sales_date, store_name, shop_type, business_type, brand_nm, goods_no, option_nm,
                 cat_top, cat_large, cat_medium, off_md_id
    """)
    df = run_df(q)
    df["sales_date"] = pd.to_datetime(df["sales_date"], errors="coerce")
    df["goods_no"] = pd.to_numeric(df["goods_no"], errors="coerce").fillna(0).astype("int64")
    for c in ("qty", "gmv", "normal_amt", "pay", "foreign_gmv"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in ("store_name", "shop_type", "business_type", "brand_nm", "goods_nm", "option_nm",
              "sku_id", "cat_top", "cat_large", "cat_medium", "off_md_id"):
        df[c] = df[c].fillna("")
    df = apply_category_override(df)   # 크록스 지비츠(정상가 0~25900) Shoes→Acc 등 카탈로그 오탐 보정
    df = apply_running_flag(df)        # is_running: RUN 매장 취급 신발=러닝화
    df = apply_concept(df)             # concept: 마케팅 컨셉 dim(걸즈/영/포멀/킥스/뷰티/잡화/포우먼/기타)
    df.loc[df["brand_nm"] == "수베니어샵", "qty"] = 0.0
    return df[df["sales_date"].notna()]


def fetch_receipts(since: str | None = None) -> pd.DataFrame:
    """객단가용 영수증(주문) 단위 집계. 그레인 = 주문ID × 판매일 × 매장 × 매장타입 × 내외국인
       × 사업구분 × 카테(최상위/대/중) × 브랜드.  → 카테/브랜드/사업구분 필터별 객단가 지원.
       객단가 = SUM(order_amount)(분자) / COUNT(DISTINCT order_id)(분모).
       ※ order_id 단위로 저장하고 분모를 COUNT(DISTINCT)로 세므로, 한 주문이 여러 카테/브랜드에
         걸쳐 여러 행이 돼도 전체/멀티선택 객단가가 중복합산 없이 정확히 보존된다(검증 2026-06-29:
         전체 객단가 신=구 93,112 일치).
       모집단 = 완료주문(dummy_order=0, status=50, 오프라인). 주문행 sgn=+1(gross), 환불행 sgn=-1.
         · 객단가/시간대/요일 = gross(sgn=1만) — 기존과 동일(영수증 시점 금액).
         · 순매출(일자 스냅샷 등) = SUM(sgn*gmv) — fetch_sales와 동일 기준(환불 반영).
         환불행: sales_date=환불처리일(claim.created_at), hour=그날 환불시각(대표=MAX), 금액=원주문액(fetch_sales ref와 동일 그레인).
       브랜드/카테 해석은 fetch_sales와 동일(brand=goods_master 우선→dim_brand, cat=catmap).
       since='YYYY-MM-DD' 주면 증분(그 날짜 이후 거래/환불)."""
    ordf = f"AND CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) >= '{since}'" if since else ""
    reff = f"AND CAST(created_at AS DATE) >= '{since}'" if since else ""
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
        ord AS (   -- 총주문 라인(sgn=+1). 거래시각 시(KST).
          SELECT om.order_id,
                 CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) AS sales_date,
                 HOUR(COALESCE(om.transaction_at, om.created_at)) AS hour,
                 om.shop_no, oo.goods_no, oo.company_id, oo.brand_id,
                 (CASE WHEN om.tax_refund_type IS NOT NULL AND om.tax_refund_type <> 'NONE' THEN 1 ELSE 0 END) AS is_foreign,
                 oo.order_amount, oo.quantity, 1 AS sgn
          FROM ocmp.moss.order_option oo
          JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
          WHERE om.dummy_order = 0 AND om.order_status = 50 """ + ordf + r"""
        ),
        refclaim AS (   -- 환불 처리일·대표시각(그날 최종 환불시각). fetch_sales와 동일 그레인(order×환불일).
          SELECT order_id, CAST(created_at AS DATE) AS refund_date, MAX(HOUR(created_at)) AS refund_hour
          FROM ocmp.moss.claim WHERE claim_type = 'REFUND' """ + reff + r"""
          GROUP BY order_id, CAST(created_at AS DATE)
        ),
        ref AS (   -- 환불 라인(sgn=-1). 원주문 옵션 금액을 환불처리일/시각에 음수 귀속.
          SELECT om.order_id, rc.refund_date AS sales_date, rc.refund_hour AS hour,
                 om.shop_no, oo.goods_no, oo.company_id, oo.brand_id,
                 (CASE WHEN om.tax_refund_type IS NOT NULL AND om.tax_refund_type <> 'NONE' THEN 1 ELSE 0 END) AS is_foreign,
                 oo.order_amount, oo.quantity, -1 AS sgn
          FROM refclaim rc
          JOIN ocmp.moss.order_master om ON om.order_id = rc.order_id
          JOIN ocmp.moss.order_option oo ON oo.order_id = rc.order_id
          WHERE om.dummy_order = 0
        ),
        lines AS (SELECT * FROM ord UNION ALL SELECT * FROM ref),
        gmbrand AS (   -- 상품마스터 브랜드(fetch_sales 우선순위와 동일). 주문/환불에 등장한 goods만.
          SELECT CAST(g.goods_no AS BIGINT) AS goods_no, ANY_VALUE(b2.brand_nm) AS gm_brand_nm
          FROM musinsa.bizest.goods g
          LEFT JOIN musinsa.partnerportal.brand b2 ON b2.brand = g.brand
          WHERE CAST(g.goods_no AS BIGINT) IN (SELECT DISTINCT CAST(goods_no AS BIGINT) FROM lines)
          GROUP BY g.goods_no
        )
        SELECT order_id, sales_date, store_name, shop_type, is_foreign,
               business_type, brand_nm, cat_top, cat_large, cat_medium, hour, sgn,
               CAST(SUM(order_amount) AS DOUBLE) AS gmv, CAST(SUM(quantity) AS DOUBLE) AS qty
        FROM (
          SELECT o.order_id, o.sales_date, o.hour, o.sgn, st.store_name, st.shop_type, o.is_foreign,
                 COALESCE(br.business_type, '기타') AS business_type,
                 COALESCE(NULLIF(gm.gm_brand_nm, ''), br.brand_nm, '(미매칭)') AS brand_nm,
                 COALESCE(cat.cat_top, '미분류')    AS cat_top,
                 COALESCE(cat.cat_large, '미분류')  AS cat_large,
                 COALESCE(cat.cat_medium, '미분류') AS cat_medium,
                 o.order_amount, o.quantity
          FROM lines o
          JOIN dim_store st ON st.shop_no = o.shop_no
          LEFT JOIN dim_brand br ON br.com_id = o.company_id AND br.brand_code = o.brand_id
          LEFT JOIN catmap   cat ON cat.goods_no = o.goods_no
          LEFT JOIN gmbrand  gm  ON gm.goods_no = CAST(o.goods_no AS BIGINT)
        ) t
        GROUP BY order_id, sales_date, store_name, shop_type, is_foreign,
                 business_type, brand_nm, cat_top, cat_large, cat_medium, hour, sgn
    """
    df = run_df(q)
    df["order_id"] = df["order_id"].astype(str)
    df["sales_date"] = pd.to_datetime(df["sales_date"])
    df["hour"] = pd.to_numeric(df["hour"], errors="coerce").fillna(-1).astype("int64")  # 거래시각 시(0~23, 불명 -1)
    df["is_foreign"] = pd.to_numeric(df["is_foreign"]).fillna(0).astype("int64")
    df["sgn"] = pd.to_numeric(df["sgn"], errors="coerce").fillna(1).astype("int64")  # +1 주문 / -1 환불
    df["gmv"] = pd.to_numeric(df["gmv"]).fillna(0.0)
    if "qty" in df.columns:
        df["qty"] = pd.to_numeric(df["qty"]).fillna(0.0)
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
    """상품 마스터: goods_no → goods_nm(상품명)·brand_nm(브랜드명)·정상가·판매가·style_no·등록일.
    ⚠️ 오프라인에서 주문된 적 있는 goods_no만 적재(전 상품 600만 → 약 12만). sales/inventory/cmptab/
       prodmeta가 참조하는 건 '오프라인에서 팔린 상품'뿐이라(판매 행의 goods_no는 100% 이 집합에 포함)
       enrichment 결과는 동일하면서 메모리/스토리지를 ~52배 줄인다 → 작은 인스턴스 갱신(scale-to-zero) 가능.
    다중소스 보강: sold 기준 LEFT JOIN → bizest.goods(메인) → itgg.goods(상품명/브랜드/가격 폴백) →
       goods_sale_price_changes(온라인 1차세일가). bizest 누락 판매 goods도 통째 빠지지 않고 엔트리 생성.
       판매가 = 온라인 1차세일가(폴백 bizest→itgg→정상가). brand_nm은 brand코드→partnerportal.brand 조인."""
    q = "WITH " + DIM_STORE + r""",
        sold AS (   -- 오프라인 '판매된' ∪ '현재고 보유' goods (재고만 있고 미판매 상품도 스타일/가격 보강 위해 포함)
          SELECT DISTINCT CAST(oo.goods_no AS BIGINT) AS goods_no
          FROM ocmp.moss.order_option oo
          JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
          JOIN dim_store st ON st.shop_no = om.shop_no
          WHERE om.dummy_order = 0
          UNION   -- 재고 스냅샷 최신 보유 goods(오프라인 매장·창고) — 미판매 재고상품 누락 방지
          SELECT DISTINCT CAST(s.goods_no AS BIGINT) AS goods_no
          FROM team.sales.dsh_d_upt_editorial_stock_summary s
          JOIN (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary) le
            ON s.ord_state_date = le.d
          WHERE s.goods_no IS NOT NULL
        ),
        bad_px AS (   -- 손상 방어: 원천에 '한 시각에 판매가가 2개 이상 충돌하는 (goods,시각)' 배치가 존재
                      -- (예 goods 5943429, 2026-08-08 14:56에 91·155·2385000… 13행). 최신이 이 배치면 엉뚱값 선택 →
                      -- 해당 (goods,시각)을 통째 제외하고 그 다음 정상 시각의 값을 쓴다.
          SELECT goods_id, created_at
          FROM musinsa.itgg.goods_sale_price_changes
          WHERE channel_id = 1 AND CAST(goods_id AS BIGINT) IN (SELECT goods_no FROM sold)
          GROUP BY goods_id, created_at HAVING COUNT(DISTINCT sale_price) > 1
        ),
        px AS (   -- 온라인 1차세일가·정상가(현재가) = 손상 배치 제외 후 상품별 최신 1건 (channel_id=1).
          SELECT CAST(p.goods_id AS BIGINT) AS goods_no, p.sale_price AS online_sale, p.basic_price AS online_basic
          FROM musinsa.itgg.goods_sale_price_changes p
          LEFT ANTI JOIN bad_px b ON b.goods_id = p.goods_id AND b.created_at = p.created_at
          WHERE p.channel_id = 1 AND CAST(p.goods_id AS BIGINT) IN (SELECT goods_no FROM sold)
          QUALIFY row_number() OVER (PARTITION BY p.goods_id ORDER BY p.created_at DESC) = 1
        ),
        ig AS (   -- 폴백 소스: bizest.goods에 없는 판매 goods의 상품명/브랜드명/가격 (goods_id별 최신 1건)
          SELECT CAST(goods_id AS BIGINT) AS goods_no, goods_name AS goods_nm, brand_name AS brand_nm,
                 basic_price AS ig_basic, sale_price AS ig_sale
          FROM musinsa.itgg.goods
          WHERE goods_id IS NOT NULL
          QUALIFY row_number() OVER (PARTITION BY goods_id ORDER BY updated_at DESC) = 1
        )
        -- sold 기준 LEFT JOIN + 다중소스 COALESCE → 오프라인 판매 goods는 100% 엔트리(누락 0).
        SELECT sg.goods_no,
               COALESCE(ANY_VALUE(g.goods_nm), ANY_VALUE(ig.goods_nm)) AS goods_nm,
               COALESCE(ANY_VALUE(b.brand_nm), ANY_VALUE(ig.brand_nm)) AS brand_nm,
               MIN(CAST(g.reg_dm AS DATE)) AS reg_date,          -- 등록일·스타일넘버는 bizest만 보유
               ANY_VALUE(g.style_no) AS style_no,
               ANY_VALUE(g.img) AS img, ANY_VALUE(CAST(g.img_update AS STRING)) AS img_update,   -- 대표이미지(bizest)
               CAST(COALESCE(ANY_VALUE(g.normal_price), ANY_VALUE(px.online_basic), ANY_VALUE(ig.ig_basic)) AS DOUBLE) AS normal_price,
               -- 판매가 = 온라인 1차세일가 → bizest price → itgg sale → 정상가/basic 폴백 (모두 없으면 NULL)
               CAST(COALESCE(ANY_VALUE(px.online_sale), ANY_VALUE(g.price), ANY_VALUE(ig.ig_sale),
                             ANY_VALUE(g.normal_price), ANY_VALUE(px.online_basic), ANY_VALUE(ig.ig_basic)) AS DOUBLE) AS sale_price
        FROM sold sg
        LEFT JOIN musinsa.bizest.goods g ON CAST(g.goods_no AS BIGINT) = sg.goods_no
        LEFT JOIN musinsa.partnerportal.brand b ON b.brand = g.brand
        LEFT JOIN px ON px.goods_no = sg.goods_no
        LEFT JOIN ig ON ig.goods_no = sg.goods_no
        GROUP BY sg.goods_no
    """
    d = run_df(q)
    d["goods_no"] = pd.to_numeric(d["goods_no"]).astype("int64")
    d["goods_nm"] = d["goods_nm"].fillna("")
    d["brand_nm"] = d["brand_nm"].fillna("")
    d["reg_date"] = pd.to_datetime(d["reg_date"], errors="coerce")  # 상품 등록일(reg_dm) — 신규 판정 기준
    d["style_no"] = d["style_no"].fillna("")
    d["img"] = d["img"].fillna("") if "img" in d.columns else ""
    d["img_update"] = d["img_update"].fillna("") if "img_update" in d.columns else ""
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


def fetch_settlement(since: str | None = None) -> pd.DataFrame:
    """오프라인 편집샵 순이익(Net Take)·공헌이익(CP) — team.sales.dsh_d_upt_editorial_summary_v.
       그레인: 일자×매장×상품(goods_no). Net Take=profit(순이익), CP=contribution_profit_pre(공헌이익).
       검증 항등식(2026-06-21): CP ≈ profit − offline_cost_fixed + additional_rev.
       ⚠️ CP는 매장 고정비(offline_cost_fixed) 배부값이라 최근 ~2개월은 예측치(변동)·상품 1건당 음수 가능.
       대시보드 매장(dim_store)만. GMV(MOSS 기반)와 별개 지표로 추가.
       since='YYYY-MM-DD' 주면 그 날짜 이후만(증분 갱신용). None=전체."""
    df = f"AND v.ord_state_date >= '{since}'" if since else ""
    q = ("WITH " + DIM_STORE + r"""
        SELECT v.ord_state_date AS sales_date, ds.store_name, ds.shop_type,
               CAST(v.goods_no AS BIGINT) AS goods_no,
               CAST(SUM(v.profit) AS DOUBLE) AS net_take,
               CAST(SUM(v.contribution_profit_pre) AS DOUBLE) AS cp
        FROM team.sales.dsh_d_upt_editorial_summary_v v
        JOIN dim_store ds ON ds.shop_no = v.shop_no
        WHERE v.ord_state_date IS NOT NULL AND v.goods_no IS NOT NULL """ + df + r"""
        GROUP BY 1, 2, 3, 4
    """)
    d = run_df(q)
    d["sales_date"] = pd.to_datetime(d["sales_date"], errors="coerce")
    d["goods_no"] = pd.to_numeric(d["goods_no"], errors="coerce").fillna(0).astype("int64")
    d["net_take"] = pd.to_numeric(d["net_take"], errors="coerce").fillna(0.0)
    d["cp"] = pd.to_numeric(d["cp"], errors="coerce").fillna(0.0)
    return d[d["sales_date"].notna()]


def fetch_settlement_option(since: str | None = None) -> pd.DataFrame:
    """오프라인 편집샵 정산 상세(옵션/사이즈 단위) — team.sales.dsh_d_upt_editorial_summary_v.
       그레인: 일자×매장×상품×옵션(option_nm). CSV(일자·옵션별 순이익·CP·정산GMV) 전용.
       ⚠️ gmv=ord_amt(정산기준, MOSS 화면 GMV와 다를 수 있음). Net Take=profit, CP=contribution_profit_pre.
       대시보드 매장(dim_store)만. net_take 요약은 goods 단위 settlement 캐시가 담당(여긴 CSV 상세용).
       since='YYYY-MM-DD' 주면 그 날짜 이후만(증분 갱신용). None=전체."""
    df = f"AND v.ord_state_date >= '{since}'" if since else ""
    q = ("WITH " + DIM_STORE + r"""
        SELECT v.ord_state_date AS sales_date, ds.store_name, ds.shop_type,
               v.brand_nm, v.large_nm AS cat_large, v.medium_nm AS cat_medium,
               CAST(v.goods_no AS BIGINT) AS goods_no, ANY_VALUE(v.goods_nm) AS goods_nm, v.option_nm,
               CAST(SUM(v.qty) AS DOUBLE)          AS qty,
               CAST(SUM(v.ord_amt) AS DOUBLE)      AS gmv,
               CAST(SUM(v.normal_amt) AS DOUBLE)   AS normal_amt,
               CAST(SUM(v.pay_amt) AS DOUBLE)      AS pay,
               CAST(SUM(v.profit) AS DOUBLE)       AS net_take,
               CAST(SUM(v.contribution_profit_pre) AS DOUBLE) AS cp
        FROM team.sales.dsh_d_upt_editorial_summary_v v
        JOIN dim_store ds ON ds.shop_no = v.shop_no
        WHERE v.ord_state_date IS NOT NULL AND v.goods_no IS NOT NULL """ + df + r"""
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 9
    """)
    d = run_df(q)
    d["sales_date"] = pd.to_datetime(d["sales_date"], errors="coerce")
    d["goods_no"] = pd.to_numeric(d["goods_no"], errors="coerce").fillna(0).astype("int64")
    for c in ("qty", "gmv", "normal_amt", "pay", "net_take", "cp"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    for c in ("brand_nm", "cat_large", "cat_medium", "goods_nm", "option_nm", "store_name", "shop_type"):
        d[c] = d[c].fillna("")
    return d[d["sales_date"].notna()]


def fetch_settlement_daily(since: str | None = None) -> pd.DataFrame:
    """오프라인 손익(P&L) 일자×매장×브랜드×사업구분×카테(최상위/대/중) 집계 — team.sales.dsh_d_upt_editorial_summary_v.
       Net Take=profit, CP=contribution_profit_pre, GMV=ord_amt(정산), 매장고정비=offline_cost_fixed(goods별 배분=가산 검증).
       ★사업구분·카테는 손익 탭 **백단 필터용**(표엔 매장/브랜드만 노출) — sales와 동일 매핑(company margin_type / catmap)으로
         붙여 공통 FilterBar 값과 일치. 표시는 /api/pnl에서 매장 또는 브랜드로 재집계(모든 지표 가산이라 정확).
       전월·전년동월 비교 위해 전체 이력(2023-10~) 보관. 대시보드 매장(dim_store)만.
       ⚠️ 최근 ~2개월 CP·매장고정비는 예측(잠정) — SAP 실적 확정 전.
       since='YYYY-MM-DD' 주면 그 날짜 이후만(증분). 과거는 증분 병합이 보존(전월/전년 비교 유지)."""
    df = f"AND v.ord_state_date >= '{since}'" if since else ""
    q = ("WITH " + DIM_STORE + r""",
        dim_brand AS (   -- 사업구분(위탁/매입) = company margin_type (fetch_sales와 동일 → 공통 필터 값 일치)
          SELECT cb.com_id, cb.brand AS brand_code,
            CASE c.margin_type WHEN 'FEE' THEN '위탁' WHEN 'WONGA' THEN '매입' ELSE '기타' END AS business_type
          FROM musinsa.partnerportal.company_brand cb
          LEFT JOIN musinsa.partnerportal.company c ON c.com_id = cb.com_id
        ),
        catmap AS (      -- 카테(최상위/대/중) = 에디토리얼 최신 스냅샷 (fetch_sales와 동일 → 공통 필터 값 일치)
          SELECT goods_no, ANY_VALUE(final_large_nm_off) AS cat_top,
                 ANY_VALUE(large_nm_off) AS cat_large, ANY_VALUE(medium_nm_off) AS cat_medium
          FROM team.sales.dsh_d_upt_editorial_stock_summary s2
          JOIN (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary) lc
            ON s2.ord_state_date = lc.d
          GROUP BY goods_no
        )
        SELECT v.ord_state_date AS sales_date, ds.store_name, ds.shop_type, v.brand_nm,
               COALESCE(br.business_type, '기타') AS business_type,
               COALESCE(cat.cat_top, '미분류')    AS cat_top,
               COALESCE(cat.cat_large, '미분류')  AS cat_large,
               COALESCE(cat.cat_medium, '미분류') AS cat_medium,
               CAST(SUM(v.qty) AS DOUBLE)          AS qty,
               CAST(SUM(v.ord_amt) AS DOUBLE)      AS gmv,
               CAST(SUM(v.normal_amt) AS DOUBLE)   AS normal_amt,
               CAST(SUM(v.pay_amt) AS DOUBLE)      AS pay,
               CAST(SUM(v.profit) AS DOUBLE)       AS net_take,
               CAST(SUM(v.contribution_profit_pre) AS DOUBLE) AS cp,
               CAST(SUM(v.offline_cost_fixed) AS DOUBLE)      AS offline_cost,
               CAST(SUM(v.additional_rev) AS DOUBLE)          AS add_rev
        FROM team.sales.dsh_d_upt_editorial_summary_v v
        JOIN dim_store ds ON ds.shop_no = v.shop_no
        LEFT JOIN dim_brand br ON br.com_id = v.company_id AND br.brand_code = v.brand_id
        LEFT JOIN catmap cat ON cat.goods_no = v.goods_no
        WHERE v.ord_state_date IS NOT NULL """ + df + r"""
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
    """)
    d = run_df(q)
    d["sales_date"] = pd.to_datetime(d["sales_date"], errors="coerce")
    for c in ("qty", "gmv", "normal_amt", "pay", "net_take", "cp", "offline_cost", "add_rev"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    for c in ("store_name", "shop_type", "brand_nm", "business_type", "cat_top", "cat_large", "cat_medium"):
        d[c] = d[c].fillna("")
    return d[d["sales_date"].notna()]


# ---------------------------------------------------------------------------
# 통합 IPS (브랜드 × 구분[매입/위탁] × 실) — Apps Script "통합IPS" 이식.
#   원천: 온라인 orders_merged_yoy_new + 오프라인 editorial_summary_v
#         + 재고 dsh_sku_stock_history_by_storage(1P/3P/MFS 일별) + goods/editorial_stock 정상가.
#   주차: W0=직전 완료주(월~일). 재고 스냅샷 w0~w3/base_mon. current_date 기준 시간상대(=매일 갱신).
#   ⚠️ 킥스 범위(대상 브랜드·매장)는 원천 데이터로 도출 불가 → 아래 상수 리스트로 고정(스크립트와 동일).
#      매장코드는 werks별 재사용 위험 있어 (코드+매장명) 쌍으로 필터(STORE_PAIRS).
# ---------------------------------------------------------------------------
# 풋웨어/플레이어실 외에도 반드시 포함할 업체코드(타 실 소속 킥스 취급 브랜드)
IPS_EXTRA_COM_LIST = [
    'rockfish', 'ept', 'yase', 'pemont', 'tretorn', 'kinchi', 'amersports_salomon', 'brusher', 'iyso',
    'joycompany', 'khiho', 'samwoo', 'customade', 'freeduck', 'jillbyjillstuart', 'komorebimuseum',
    'kswisskorea', 'macmoc', 'nuus', 'snc2012', 'vatoz', 'ihcnik', 'eland', 'hilightbrands_2',
    'coreofalchemy', 'foggymoody', 'jaclar', 'bearpaw', 'kristin', 'samwoohc1', 'brandworkskorea', 'menat',
]
# [매입] ERP lgort 4자리 + 재고이력 storage_loc_nm (werks1000 킥스/에디토리얼 플랜트 기준)
IPS_STORE_1P = [
    ('3040', 'MSS_대구'), ('3050', 'MSS_홍대'), ('3055', '무신사킥스_홍대'), ('3057', '무신사킥스_성수'),
    ('3058', '무신사킥스_스타필드 고양점'), ('3150', '무신사 런_서울숲'), ('3160', 'MSS_무신사 스토어 강남'),
    ('3170', 'MSS_AK플라자 수원점'), ('3360', 'MSS 무신사 스토어 성수'), ('3520', 'MSS_걸즈 타임스퀘어'),
    ('3530', '메가스토어_아이파크몰_용산'), ('3532', '메가스토어_성수'), ('3580', 'MSS_명동'),
    ('3590', 'MSS_롯데백화점 잠실'), ('3600', '아울렛&유즈드_롯데몰_은평'), ('3650', 'MSS_백앤캡클럽_서울숲점'),
    ('3660', 'MSS_트리플스트리트_송도점'),
]
# [위탁·MFS] SS03 코드 — 위 매장의 대응 코드
IPS_STORE_3P = [
    ('SS030025', '무신사 스토어 대구'), ('SS030026', '무신사 스토어 홍대'), ('SS030027', '무신사 스토어 성수@대림창고'),
    ('SS030042', '무신사 스토어 강남'), ('SS030054', '무신사 걸즈 타임스퀘어 영등포점'),
    ('SS030055', '무신사 메가스토어 아이파크몰 용산점'), ('SS030059', '무신사킥스 홍대'),
    ('SS030060', '무신사 스토어 롯데백화점 잠실점'), ('SS030061', '무신사 스토어 명동'),
    ('SS030068', '무신사 아울렛&유즈드 롯데몰은평'), ('SS030069', '무신사 메가스토어 성수'),
    ('SS030070', '무신사 킥스 성수'), ('SS030071', '무신사 백앤캡클럽 서울숲'), ('SS030076', '무신사 런 서울숲'),
    ('SS030077', '무신사 스토어 AK플라자 수원점'), ('SS030080', '무신사 스토어 트리플스트리트 송도점'),
    ('SS030107', '무신사 킥스 스타필드 고양점'),
]
# 매입(1P) 벤더 업체코드 — 재고 기준. (실적 com_id는 판매주체라 재고 벤더코드와 다름)
IPS_PUR_COM_LIST = [
    'adidas', 'nikekorea2', 'puma_1', 'crocs', 'oakely_ftw', 'siv', 'vans', 'underarmourkorea', 'hoka',
    'merrell_1', 'saucony_ftw', 'keen_1p', 'mizunokorea2', 'converse', 'reebok', 'asics', 'etc', 'drmartens',
    'amersports', 'sorel_ftw', 'scarpa_1', 'clarks_ftw', 'oofos', 'bnftrading', 'astorflex', 'timberland',
    'runcollection', 'redwing', 'posteam_1p', 'newbalance_1', 'paes_1', 'thehcorporation', 'districtvision_1',
    'puma_p', 'birkenstock_1p', 'garminkorea', 'moonstar_ftw', 'islandslipper', 'theartfulstore',
]
IPS_EXCLUDE_COM_LIST = ['musinsa_used']


def _ips_qlist(items) -> str:
    """SQL IN 리터럴 목록. 빈 목록은 '' (매칭 없음)."""
    return ",".join("'" + str(x).replace("'", "''") + "'" for x in items) if items else "''"


def _ips_store_pairs() -> str:
    pairs = IPS_STORE_1P + IPS_STORE_3P
    return " OR ".join(
        f"(h.storage_loc_cd='{c}' AND h.storage_loc_nm='{n.replace(chr(39), chr(39) * 2)}')"
        for c, n in pairs
    )


def _ips_p1700_locs() -> list[str]:
    """플랜트1700 매장성 lgort 선행 조회 → 메인 쿼리에 리터럴 주입(대용량 ERP 스캔 회피)."""
    q = r"""
        SELECT DISTINCT lgort
        FROM datamart.erp.inventory_closing_latest
        WHERE buper IN (date_format(current_date(),'yyyyMM'), date_format(add_months(current_date(),-1),'yyyyMM'))
          AND werks='1700' AND lgort LIKE '3%'
          AND lgobe NOT LIKE '%온라인몰%' AND lgobe NOT LIKE '%홀세일%'
    """
    return [str(x) for x in run_df(q)["lgort"].tolist()]


def _ips_pur_brands(p1700: list[str]) -> list[str]:
    """매입 행을 반드시 생성해야 하는 브랜드(매입벤더·플랜트1700 재고 보유)를 선행 조회."""
    p1700_lit = _ips_qlist(p1700)
    q = f"""
        SELECT DISTINCT g.brand
        FROM team.sales.dsh_sku_stock_history_by_storage h
        JOIN datamart.datamart.goods g ON h.product_no=CAST(g.goods_no AS STRING)
        WHERE h.base_date=LEAST(DATE_TRUNC('WEEK', CURRENT_DATE()),
                                (SELECT MAX(base_date) FROM team.sales.dsh_sku_stock_history_by_storage))
          AND h.platform='MUSINSA' AND h.biz_type='1P'
          AND COALESCE(h.com_id,'') NOT IN ({_ips_qlist(IPS_EXCLUDE_COM_LIST)})
          AND (h.com_id IN ({_ips_qlist(IPS_PUR_COM_LIST)}) OR h.storage_loc_cd IN ({p1700_lit}))
          AND g.brand IS NOT NULL
    """
    return [str(x) for x in run_df(q)["brand"].tolist()]


def _ips_wk(tbl: str, expr: str, alias: str) -> str:
    """주차별(W0~W3) SUM(CASE ...) 4개 컬럼 생성. tbl=실적테이블 별칭(o/ev)."""
    return ",\n    ".join(
        f"SUM(CASE WHEN {tbl}.ord_state_date BETWEEN wk.w{i}s AND wk.w{i}e THEN {expr} ELSE 0 END) AS {alias}_w{i}"
        for i in range(4)
    )


def _ips_build_sql(p1700: list[str], purb: list[str]) -> str:
    P1700 = _ips_qlist(p1700)
    PURB = _ips_qlist(purb)
    EXTRA = _ips_qlist(IPS_EXTRA_COM_LIST)
    EXCLUDE = _ips_qlist(IPS_EXCLUDE_COM_LIST)
    PAIRS = _ips_store_pairs()
    onQty = _ips_wk('o', 'o.sell_sub_clm_qty', 'on_qty')
    onGmv = _ips_wk('o', 'o.normal_gmv', 'on_gmv')
    onNt = _ips_wk('o', 'o.revenue', 'on_nt')
    onCp = _ips_wk('o', 'o.contribution_profit_pre', 'on_cp')
    offQty = _ips_wk('ev', 'ev.sell_qty-ev.clm_qty', 'off_qty')
    offGmv = _ips_wk('ev', 'ev.ord_amt', 'off_gmv')
    offNt = _ips_wk('ev', 'ev.profit', 'off_nt')
    offCp = _ips_wk('ev', 'ev.contribution_profit', 'off_cp')
    return f"""
WITH wk AS (
  SELECT
    DATE_TRUNC('WEEK', CURRENT_DATE())              AS ws,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),7)  AS w0s, DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),1)  AS w0e,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),14) AS w1s, DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),8)  AS w1e,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),21) AS w2s, DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),15) AS w2e,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),28) AS w3s, DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),22) AS w3e,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),56) AS p4s, DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),29) AS p4e,
    LEAST(DATE_TRUNC('WEEK', CURRENT_DATE()),
          (SELECT MAX(base_date) FROM team.sales.dsh_sku_stock_history_by_storage)) AS w0,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),7)  AS w1,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),14) AS w2,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),21) AS w3,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),28) AS base_mon,
    LEAST(DATE_TRUNC('WEEK', CURRENT_DATE()),
          (SELECT MAX(ord_state_date) FROM team.sales.dsh_d_upt_editorial_stock_summary)) AS con_dt
),
pur_brands AS (SELECT explode(array({PURB})) AS brand_id),
onl_raw AS (
  SELECT o.brand AS brand_id, o.ord_com_type, o.hier,
    MAX(o.com_id) AS com_id,
    SUM(CASE WHEN o.ord_state_date BETWEEN wk.w3s AND wk.w0e THEN o.normal_gmv ELSE 0 END) AS gmv4,
    {onQty},
    {onGmv},
    {onNt},
    {onCp},
    SUM(CASE WHEN o.ord_state_date>=wk.base_mon THEN o.mem_dc+o.cou_dc+o.cart_cou_dc ELSE 0 END) AS coupon_m0,
    SUM(CASE WHEN o.ord_state_date BETWEEN wk.w3s AND wk.w0e THEN o.normal_ggmv ELSE 0 END) AS on_nggmv_4w,
    SUM(CASE WHEN o.ord_state_date BETWEEN wk.p4s AND wk.p4e THEN o.normal_gmv ELSE 0 END) AS gmv_prev4w,
    SUM(CASE WHEN o.ord_state_date BETWEEN wk.p4s AND wk.p4e THEN o.sell_sub_clm_qty ELSE 0 END) AS qty_prev4w
  FROM team.sales.dsh_d_upt_orders_merged_yoy_new o CROSS JOIN wk
  WHERE (
      o.hier IN ('무신사 풋웨어실','무신사 플레이어실')
      OR o.com_id IN ({EXTRA})
      OR (o.ord_com_type='공급(매입)' AND o.brand IN (SELECT brand_id FROM pur_brands))
    )
    AND COALESCE(o.com_id,'') NOT IN ({EXCLUDE})
    AND o.ord_state_date BETWEEN wk.p4s AND wk.w0e
  GROUP BY o.brand, o.ord_com_type, o.hier
),
onl_hier AS (SELECT brand_id, ord_com_type, hier, gmv4 FROM onl_raw),
onl AS (
  SELECT brand_id, ord_com_type,
    MAX(hier) AS hier, MAX(com_id) AS com_id,
    SUM(on_qty_w0) AS on_qty_w0, SUM(on_qty_w1) AS on_qty_w1, SUM(on_qty_w2) AS on_qty_w2, SUM(on_qty_w3) AS on_qty_w3,
    SUM(on_gmv_w0) AS on_gmv_w0, SUM(on_gmv_w1) AS on_gmv_w1, SUM(on_gmv_w2) AS on_gmv_w2, SUM(on_gmv_w3) AS on_gmv_w3,
    SUM(on_nt_w0)  AS on_nt_w0,  SUM(on_nt_w1)  AS on_nt_w1,  SUM(on_nt_w2)  AS on_nt_w2,  SUM(on_nt_w3)  AS on_nt_w3,
    SUM(on_cp_w0)  AS on_cp_w0,  SUM(on_cp_w1)  AS on_cp_w1,  SUM(on_cp_w2)  AS on_cp_w2,  SUM(on_cp_w3)  AS on_cp_w3,
    SUM(coupon_m0) AS coupon_m0, SUM(on_nggmv_4w) AS on_nggmv_4w,
    SUM(gmv_prev4w) AS gmv_prev4w, SUM(qty_prev4w) AS qty_prev4w
  FROM onl_raw GROUP BY brand_id, ord_com_type
),
offl AS (
  SELECT ev.brand_id, ev.ord_com_type,
    {offQty},
    {offGmv},
    {offNt},
    {offCp},
    SUM(CASE WHEN ev.ord_state_date BETWEEN wk.w3s AND wk.w0e THEN ev.normal_amt ELSE 0 END) AS off_namt_4w,
    SUM(CASE WHEN ev.ord_state_date BETWEEN wk.w3s AND wk.w0e THEN ev.coupon_dc_amt ELSE 0 END) AS off_coupon_4w,
    SUM(CASE WHEN ev.ord_state_date BETWEEN wk.p4s AND wk.p4e THEN ev.ord_amt ELSE 0 END) AS off_gmv_prev4w,
    SUM(CASE WHEN ev.ord_state_date BETWEEN wk.p4s AND wk.p4e THEN ev.sell_qty-ev.clm_qty ELSE 0 END) AS off_qty_prev4w
  FROM team.sales.dsh_d_upt_editorial_summary_v ev CROSS JOIN wk
  WHERE ev.ord_state_date BETWEEN wk.p4s AND wk.w0e
    AND ev.ord_com_type IS NOT NULL
  GROUP BY ev.brand_id, ev.ord_com_type
),
rep_hier AS (
  SELECT brand_id, ord_com_type, hier FROM (
    SELECT brand_id, ord_com_type, hier,
      ROW_NUMBER() OVER (PARTITION BY brand_id, ord_com_type ORDER BY gmv4 DESC) rn
    FROM onl_hier WHERE hier IS NOT NULL AND hier<>'유즈드' AND gmv4>0
  ) WHERE rn=1
),
scope_com AS (
  SELECT DISTINCT g.com_id FROM datamart.datamart.goods g
  WHERE g.brand IN (SELECT brand_id FROM onl UNION SELECT brand_id FROM pur_brands) AND g.com_id IS NOT NULL
),
goods_scope AS (
  SELECT CAST(g.goods_no AS STRING) AS product_no, g.brand AS brand_id,
         g.brand_nm, g.normal_price
  FROM datamart.datamart.goods g
  WHERE g.brand IN (SELECT brand_id FROM onl UNION SELECT brand_id FROM pur_brands)
),
target_goods AS (SELECT product_no, brand_id FROM goods_scope),
stk_raw AS (
  SELECT tg.brand_id, h.biz_type, h.com_id,
    CASE WHEN ({PAIRS})
           OR h.storage_loc_cd IN ({P1700}) THEN 1 ELSE 0 END AS is_store,
    h.base_date, h.end_stock_qty
  FROM team.sales.dsh_sku_stock_history_by_storage h
  JOIN target_goods tg ON h.product_no=tg.product_no
  CROSS JOIN wk
  WHERE h.platform='MUSINSA' AND h.biz_type IN ('1P','3P','MFS')
    AND h.base_date IN (wk.w0, wk.w1, wk.w2, wk.w3, wk.base_mon)
    AND (h.com_id IS NULL OR h.com_id IN (SELECT com_id FROM scope_com))
    AND COALESCE(h.com_id,'') NOT IN ({EXCLUDE})
),
stk_axis AS (
  SELECT DISTINCT brand_id, CASE WHEN biz_type='1P' THEN '공급(매입)' ELSE '입점(위탁)' END AS ord_com_type
  FROM stk_raw
),
axis AS (
  SELECT brand_id, ord_com_type FROM onl
  UNION SELECT f.brand_id, f.ord_com_type FROM offl f
    WHERE f.brand_id IN (SELECT brand_id FROM onl UNION SELECT brand_id FROM pur_brands)
  UNION SELECT brand_id, ord_com_type FROM stk_axis
),
con_stk AS (
  SELECT brand_id,
    SUM(CASE WHEN base_date=(SELECT w0 FROM wk) THEN end_stock_qty ELSE 0 END) AS total_cur,
    SUM(CASE WHEN base_date=(SELECT w0 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS store_cur,
    SUM(CASE WHEN base_date=(SELECT w0 FROM wk) AND is_store=0 THEN end_stock_qty ELSE 0 END) AS logi_cur,
    SUM(CASE WHEN base_date=(SELECT base_mon FROM wk) THEN end_stock_qty ELSE 0 END) AS total_base,
    SUM(CASE WHEN base_date=(SELECT base_mon FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS store_base,
    SUM(CASE WHEN base_date=(SELECT w0 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS st_w0,
    SUM(CASE WHEN base_date=(SELECT w1 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS st_w1,
    SUM(CASE WHEN base_date=(SELECT w2 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS st_w2,
    SUM(CASE WHEN base_date=(SELECT w3 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS st_w3
  FROM stk_raw WHERE biz_type IN ('3P','MFS') GROUP BY brand_id
),
pur_stk AS (
  SELECT brand_id,
    SUM(CASE WHEN base_date=(SELECT w0 FROM wk) THEN end_stock_qty ELSE 0 END) AS total_cur,
    SUM(CASE WHEN base_date=(SELECT w0 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS store_cur,
    SUM(CASE WHEN base_date=(SELECT w0 FROM wk) AND is_store=0 THEN end_stock_qty ELSE 0 END) AS logi_cur,
    SUM(CASE WHEN base_date=(SELECT base_mon FROM wk) THEN end_stock_qty ELSE 0 END) AS total_base,
    SUM(CASE WHEN base_date=(SELECT base_mon FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS store_base,
    SUM(CASE WHEN base_date=(SELECT w0 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS st_w0,
    SUM(CASE WHEN base_date=(SELECT w1 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS st_w1,
    SUM(CASE WHEN base_date=(SELECT w2 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS st_w2,
    SUM(CASE WHEN base_date=(SELECT w3 FROM wk) AND is_store=1 THEN end_stock_qty ELSE 0 END) AS st_w3
  FROM stk_raw WHERE biz_type='1P' GROUP BY brand_id
),
goods_agg AS (SELECT brand_id, MAX(brand_nm) AS brand_nm, ROUND(AVG(NULLIF(normal_price,0))) AS normal_price FROM goods_scope GROUP BY brand_id),
con_stock_src AS (
  SELECT s.brand AS brand_id, s.com_id,
    SUM(s.sellable_qty) AS sellable_qty,
    AVG(NULLIF(s.normal_price,0)) AS avg_price
  FROM team.sales.dsh_d_upt_editorial_stock_summary s CROSS JOIN wk
  WHERE s.ord_state_date=wk.con_dt AND s.ord_com_type='입점(위탁)'
  GROUP BY s.brand, s.com_id
),
con_price AS (SELECT brand_id, ROUND(AVG(avg_price)) AS normal_price FROM con_stock_src WHERE avg_price>0 GROUP BY brand_id),
rep_pur AS (SELECT brand_id, com_id FROM (SELECT brand_id, com_id, ROW_NUMBER() OVER (PARTITION BY brand_id ORDER BY SUM(end_stock_qty) DESC) rn FROM stk_raw WHERE biz_type='1P' AND base_date=(SELECT w0 FROM wk) GROUP BY brand_id, com_id) WHERE rn=1),
rep_con AS (SELECT brand_id, com_id FROM (SELECT brand_id, com_id, ROW_NUMBER() OVER (PARTITION BY brand_id ORDER BY sellable_qty DESC) rn FROM con_stock_src WHERE COALESCE(com_id,'') NOT IN ({EXCLUDE})) WHERE rn=1),
con_rows AS (
  SELECT COALESCE(rh.hier, o.hier) AS sil, '위탁' AS gubun, CAST(a.brand_id AS STRING) AS brand_code,
    COALESCE(rcon.com_id, o.com_id,'') AS com_id, COALESCE(cm.brand_nm, CAST(a.brand_id AS STRING)) AS brand_nm,
    COALESCE(s.total_cur,0) AS total_cur, COALESCE(s.store_cur,0) AS store_cur, COALESCE(s.logi_cur,0) AS logi_cur, COALESCE(s.total_base,0) AS total_base, COALESCE(s.store_base,0) AS store_base,
    COALESCE(s.st_w0,0) AS st_w0, COALESCE(s.st_w1,0) AS st_w1, COALESCE(s.st_w2,0) AS st_w2, COALESCE(s.st_w3,0) AS st_w3, COALESCE(cp.normal_price,0) AS normal_price,
    COALESCE(o.on_qty_w0,0) AS on_qty_w0, COALESCE(o.on_qty_w1,0) AS on_qty_w1, COALESCE(o.on_qty_w2,0) AS on_qty_w2, COALESCE(o.on_qty_w3,0) AS on_qty_w3, COALESCE(o.on_gmv_w0,0) AS on_gmv_w0, COALESCE(o.on_gmv_w1,0) AS on_gmv_w1, COALESCE(o.on_gmv_w2,0) AS on_gmv_w2, COALESCE(o.on_gmv_w3,0) AS on_gmv_w3, COALESCE(o.on_nt_w0,0) AS on_nt_w0, COALESCE(o.on_nt_w1,0) AS on_nt_w1, COALESCE(o.on_nt_w2,0) AS on_nt_w2, COALESCE(o.on_nt_w3,0) AS on_nt_w3, COALESCE(o.on_cp_w0,0) AS on_cp_w0, COALESCE(o.on_cp_w1,0) AS on_cp_w1, COALESCE(o.on_cp_w2,0) AS on_cp_w2, COALESCE(o.on_cp_w3,0) AS on_cp_w3, COALESCE(o.coupon_m0,0) AS coupon_m0, COALESCE(o.on_nggmv_4w,0) AS on_nggmv_4w, COALESCE(o.gmv_prev4w,0) AS gmv_prev4w, COALESCE(o.qty_prev4w,0) AS qty_prev4w, f.off_qty_w0,f.off_qty_w1,f.off_qty_w2,f.off_qty_w3, f.off_gmv_w0,f.off_gmv_w1,f.off_gmv_w2,f.off_gmv_w3, f.off_nt_w0,f.off_nt_w1,f.off_nt_w2,f.off_nt_w3, f.off_cp_w0,f.off_cp_w1,f.off_cp_w2,f.off_cp_w3, f.off_namt_4w, f.off_coupon_4w, f.off_gmv_prev4w, f.off_qty_prev4w
  FROM axis a
  LEFT JOIN onl o ON a.brand_id=o.brand_id AND a.ord_com_type=o.ord_com_type
  LEFT JOIN offl f ON a.brand_id=f.brand_id AND a.ord_com_type=f.ord_com_type
  LEFT JOIN rep_hier rh ON a.brand_id=rh.brand_id AND a.ord_com_type=rh.ord_com_type
  LEFT JOIN con_stk s ON a.brand_id=s.brand_id
  LEFT JOIN con_price cp ON a.brand_id=cp.brand_id
  LEFT JOIN goods_agg cm ON a.brand_id=cm.brand_id
  LEFT JOIN rep_con rcon ON a.brand_id=rcon.brand_id
  WHERE a.ord_com_type='입점(위탁)'
),
pur_rows AS (
  SELECT COALESCE(rh.hier, o.hier) AS sil, '매입' AS gubun, CAST(a.brand_id AS STRING) AS brand_code,
    COALESCE(rpur.com_id, o.com_id,'') AS com_id, COALESCE(ga.brand_nm, CAST(a.brand_id AS STRING)) AS brand_nm,
    COALESCE(s.total_cur,0) AS total_cur, COALESCE(s.store_cur,0) AS store_cur, COALESCE(s.logi_cur,0) AS logi_cur, COALESCE(s.total_base,0) AS total_base, COALESCE(s.store_base,0) AS store_base,
    COALESCE(s.st_w0,0) AS st_w0, COALESCE(s.st_w1,0) AS st_w1, COALESCE(s.st_w2,0) AS st_w2, COALESCE(s.st_w3,0) AS st_w3, COALESCE(ga.normal_price,0) AS normal_price,
    COALESCE(o.on_qty_w0,0) AS on_qty_w0, COALESCE(o.on_qty_w1,0) AS on_qty_w1, COALESCE(o.on_qty_w2,0) AS on_qty_w2, COALESCE(o.on_qty_w3,0) AS on_qty_w3, COALESCE(o.on_gmv_w0,0) AS on_gmv_w0, COALESCE(o.on_gmv_w1,0) AS on_gmv_w1, COALESCE(o.on_gmv_w2,0) AS on_gmv_w2, COALESCE(o.on_gmv_w3,0) AS on_gmv_w3, COALESCE(o.on_nt_w0,0) AS on_nt_w0, COALESCE(o.on_nt_w1,0) AS on_nt_w1, COALESCE(o.on_nt_w2,0) AS on_nt_w2, COALESCE(o.on_nt_w3,0) AS on_nt_w3, COALESCE(o.on_cp_w0,0) AS on_cp_w0, COALESCE(o.on_cp_w1,0) AS on_cp_w1, COALESCE(o.on_cp_w2,0) AS on_cp_w2, COALESCE(o.on_cp_w3,0) AS on_cp_w3, COALESCE(o.coupon_m0,0) AS coupon_m0, COALESCE(o.on_nggmv_4w,0) AS on_nggmv_4w, COALESCE(o.gmv_prev4w,0) AS gmv_prev4w, COALESCE(o.qty_prev4w,0) AS qty_prev4w, f.off_qty_w0,f.off_qty_w1,f.off_qty_w2,f.off_qty_w3, f.off_gmv_w0,f.off_gmv_w1,f.off_gmv_w2,f.off_gmv_w3, f.off_nt_w0,f.off_nt_w1,f.off_nt_w2,f.off_nt_w3, f.off_cp_w0,f.off_cp_w1,f.off_cp_w2,f.off_cp_w3, f.off_namt_4w, f.off_coupon_4w, f.off_gmv_prev4w, f.off_qty_prev4w
  FROM axis a
  LEFT JOIN onl o ON a.brand_id=o.brand_id AND a.ord_com_type=o.ord_com_type
  LEFT JOIN offl f ON a.brand_id=f.brand_id AND a.ord_com_type=f.ord_com_type
  LEFT JOIN rep_hier rh ON a.brand_id=rh.brand_id AND a.ord_com_type=rh.ord_com_type
  LEFT JOIN pur_stk s ON a.brand_id=s.brand_id
  LEFT JOIN goods_agg ga ON a.brand_id=ga.brand_id
  LEFT JOIN rep_pur rpur ON a.brand_id=rpur.brand_id
  WHERE a.ord_com_type='공급(매입)'
),
allrows AS (SELECT * FROM con_rows UNION ALL SELECT * FROM pur_rows)
SELECT
  sil, gubun, brand_code, com_id, brand_nm,
  GREATEST(store_cur - store_base + COALESCE(off_qty_w0,0)+COALESCE(off_qty_w1,0)+COALESCE(off_qty_w2,0)+COALESCE(off_qty_w3,0), 0) AS inbound,
  total_base, total_cur, logi_cur,
  store_cur, store_base,
  st_w0, st_w1, st_w2, st_w3,
  ROUND((on_qty_w0+on_qty_w1+on_qty_w2+on_qty_w3+COALESCE(off_qty_w0,0)+COALESCE(off_qty_w1,0)+COALESCE(off_qty_w2,0)+COALESCE(off_qty_w3,0)) / NULLIF(on_qty_w0+on_qty_w1+on_qty_w2+on_qty_w3+COALESCE(off_qty_w0,0)+COALESCE(off_qty_w1,0)+COALESCE(off_qty_w2,0)+COALESCE(off_qty_w3,0)+total_cur,0)*100,1) AS sell_through,
  CASE WHEN (on_qty_w0+on_qty_w1+on_qty_w2+on_qty_w3+COALESCE(off_qty_w0,0)+COALESCE(off_qty_w1,0)+COALESCE(off_qty_w2,0)+COALESCE(off_qty_w3,0))>0 THEN ROUND(total_cur/((on_qty_w0+on_qty_w1+on_qty_w2+on_qty_w3+COALESCE(off_qty_w0,0)+COALESCE(off_qty_w1,0)+COALESCE(off_qty_w2,0)+COALESCE(off_qty_w3,0))/28.0),1) END AS days_all,
  CASE WHEN (COALESCE(off_qty_w0,0)+COALESCE(off_qty_w1,0)+COALESCE(off_qty_w2,0)+COALESCE(off_qty_w3,0))>0 THEN ROUND(store_cur/((COALESCE(off_qty_w0,0)+COALESCE(off_qty_w1,0)+COALESCE(off_qty_w2,0)+COALESCE(off_qty_w3,0))/28.0),1) END AS days_off,
  (on_qty_w0+COALESCE(off_qty_w0,0)) AS qty_tot_w0, (on_qty_w1+COALESCE(off_qty_w1,0)) AS qty_tot_w1, (on_qty_w2+COALESCE(off_qty_w2,0)) AS qty_tot_w2, (on_qty_w3+COALESCE(off_qty_w3,0)) AS qty_tot_w3,
  on_qty_w0 AS qty_on_w0, on_qty_w1 AS qty_on_w1, on_qty_w2 AS qty_on_w2, on_qty_w3 AS qty_on_w3,
  COALESCE(off_qty_w0,0) AS qty_off_w0, COALESCE(off_qty_w1,0) AS qty_off_w1, COALESCE(off_qty_w2,0) AS qty_off_w2, COALESCE(off_qty_w3,0) AS qty_off_w3,
  ROUND(on_gmv_w0+COALESCE(off_gmv_w0,0)) AS gmv_tot_w0, ROUND(on_gmv_w1+COALESCE(off_gmv_w1,0)) AS gmv_tot_w1, ROUND(on_gmv_w2+COALESCE(off_gmv_w2,0)) AS gmv_tot_w2, ROUND(on_gmv_w3+COALESCE(off_gmv_w3,0)) AS gmv_tot_w3,
  ROUND(on_gmv_w0) AS gmv_on_w0, ROUND(on_gmv_w1) AS gmv_on_w1, ROUND(on_gmv_w2) AS gmv_on_w2, ROUND(on_gmv_w3) AS gmv_on_w3,
  ROUND(COALESCE(off_gmv_w0,0)) AS gmv_off_w0, ROUND(COALESCE(off_gmv_w1,0)) AS gmv_off_w1, ROUND(COALESCE(off_gmv_w2,0)) AS gmv_off_w2, ROUND(COALESCE(off_gmv_w3,0)) AS gmv_off_w3,
  ROUND(on_nt_w0+COALESCE(off_nt_w0,0)) AS nt_tot_w0, ROUND(on_nt_w1+COALESCE(off_nt_w1,0)) AS nt_tot_w1, ROUND(on_nt_w2+COALESCE(off_nt_w2,0)) AS nt_tot_w2, ROUND(on_nt_w3+COALESCE(off_nt_w3,0)) AS nt_tot_w3,
  ROUND(on_nt_w0) AS nt_on_w0, ROUND(on_nt_w1) AS nt_on_w1, ROUND(on_nt_w2) AS nt_on_w2, ROUND(on_nt_w3) AS nt_on_w3,
  ROUND(COALESCE(off_nt_w0,0)) AS nt_off_w0, ROUND(COALESCE(off_nt_w1,0)) AS nt_off_w1, ROUND(COALESCE(off_nt_w2,0)) AS nt_off_w2, ROUND(COALESCE(off_nt_w3,0)) AS nt_off_w3,
  ROUND(on_cp_w0+COALESCE(off_cp_w0,0)) AS cp_tot_w0, ROUND(on_cp_w1+COALESCE(off_cp_w1,0)) AS cp_tot_w1, ROUND(on_cp_w2+COALESCE(off_cp_w2,0)) AS cp_tot_w2, ROUND(on_cp_w3+COALESCE(off_cp_w3,0)) AS cp_tot_w3,
  ROUND(on_cp_w0) AS cp_on_w0, ROUND(on_cp_w1) AS cp_on_w1, ROUND(on_cp_w2) AS cp_on_w2, ROUND(on_cp_w3) AS cp_on_w3,
  ROUND(COALESCE(off_cp_w0,0)) AS cp_off_w0, ROUND(COALESCE(off_cp_w1,0)) AS cp_off_w1, ROUND(COALESCE(off_cp_w2,0)) AS cp_off_w2, ROUND(COALESCE(off_cp_w3,0)) AS cp_off_w3,
  normal_price,
  ROUND(on_nggmv_4w + COALESCE(off_namt_4w,0)) AS normal_gmv_4w
FROM allrows
WHERE (on_gmv_w0+on_gmv_w1+on_gmv_w2+on_gmv_w3)>0
   OR (COALESCE(off_gmv_w0,0)+COALESCE(off_gmv_w1,0)+COALESCE(off_gmv_w2,0)+COALESCE(off_gmv_w3,0))>0
   OR total_cur>0
ORDER BY (on_gmv_w0+on_gmv_w1+on_gmv_w2+on_gmv_w3+COALESCE(off_gmv_w0,0)+COALESCE(off_gmv_w1,0)+COALESCE(off_gmv_w2,0)+COALESCE(off_gmv_w3,0)) DESC
"""


def _ips_inbound_po() -> pd.DataFrame:
    """PLANT 1000 외부업체 입고예정(발주잔량/open PO) — datamart.erp.mmaws0090(SAP 미착 현황).
       입고예정 = EINDT(입고예정일)>=오늘 & LOEKZ(삭제) 없음. S_MATNR→goods_no로 브랜드 매핑.
       ⚠️ 이 원천엔 수취수량 컬럼이 없어 '미래 EINDT(=미도착)'만 집계(과거 미착분 제외)."""
    q = r"""
      WITH po AS (
        SELECT CAST(S_MATNR AS STRING) AS product_no, CAST(MENGE AS DOUBLE) AS qty
        FROM datamart.erp.mmaws0090
        WHERE WERKS='1000' AND (LOEKZ IS NULL OR LOEKZ='')
          AND EINDT >= date_format(current_date(),'yyyyMMdd')
      )
      SELECT CAST(g.brand AS STRING) AS brand_code, SUM(po.qty) AS inbound_po
      FROM po JOIN datamart.datamart.goods g ON po.product_no = CAST(g.goods_no AS STRING)
      WHERE g.brand IS NOT NULL
      GROUP BY g.brand
    """
    d = run_df(q)
    d["brand_code"] = d["brand_code"].astype(str)
    d["inbound_po"] = pd.to_numeric(d["inbound_po"], errors="coerce").fillna(0.0)
    return d


# 선행조회(p1700·pur_brands) 하루 1회 메모 — fetch_ips/fetch_ips_goods가 같은 리프레시에서
# 각각 재실행(특히 pur_brands stock⨝goods ~32초)하던 중복 제거. 날짜 키라 Cloud Run 장수명에서도 안전.
_ips_prefetch_memo: dict = {"date": None, "val": None}


def _ips_prefetch() -> tuple[list[str], list[str]]:
    today = _dt.date.today().isoformat()
    if _ips_prefetch_memo["date"] != today:
        p1700 = _ips_p1700_locs()
        purb = _ips_pur_brands(p1700)
        _ips_prefetch_memo["date"] = today
        _ips_prefetch_memo["val"] = (p1700, purb)
    return _ips_prefetch_memo["val"]


def fetch_ips() -> pd.DataFrame:
    """통합 IPS 브랜드×구분 스냅샷. 선행조회(플랜트1700·매입브랜드) 후 메인 집계 1회
       + PLANT 1000 입고예정(open PO)을 매입 행에 병합."""
    p1700, purb = _ips_prefetch()
    d = run_df(_ips_build_sql(p1700, purb))
    for c in ("sil", "gubun", "brand_code", "com_id", "brand_nm"):
        d[c] = d[c].fillna("")
    # PLANT 1000 입고예정(open PO) — 매입 행에만 브랜드코드로 병합.
    try:
        po = _ips_inbound_po().set_index("brand_code")["inbound_po"].to_dict()
        d["inbound_po"] = [po.get(str(bc), 0.0) if gb == "매입" else 0.0
                           for bc, gb in zip(d["brand_code"], d["gubun"])]
    except Exception:
        d["inbound_po"] = 0.0
    num_cols = [c for c in d.columns if c not in ("sil", "gubun", "brand_code", "com_id", "brand_nm")]
    for c in num_cols:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d


def _ips_goods_sql(p1700: list[str], purb: list[str]) -> str:
    """IPS 상품단위(드릴다운) — (브랜드코드×구분×goods_no). 현재고 + 4주 판매(전체/온/오프) + 셀스루.
       브랜드 스코프·구분 매핑·매장판정은 브랜드 집계와 동일 규칙."""
    P1700 = _ips_qlist(p1700)
    PURB = _ips_qlist(purb)
    EXTRA = _ips_qlist(IPS_EXTRA_COM_LIST)
    EXCLUDE = _ips_qlist(IPS_EXCLUDE_COM_LIST)
    PAIRS = _ips_store_pairs()
    return f"""
WITH wk AS (
  SELECT
    DATE_TRUNC('WEEK', CURRENT_DATE())              AS ws,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),7)  AS w0s, DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),1) AS w0e,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),28) AS w3s,
    DATE_SUB(DATE_TRUNC('WEEK', CURRENT_DATE()),56) AS p4s,
    LEAST(DATE_TRUNC('WEEK', CURRENT_DATE()),
          (SELECT MAX(base_date) FROM team.sales.dsh_sku_stock_history_by_storage)) AS w0
),
pur_brands AS (SELECT explode(array({PURB})) AS brand_id),
brand_scope AS (
  SELECT DISTINCT o.brand AS brand_id
  FROM team.sales.dsh_d_upt_orders_merged_yoy_new o CROSS JOIN wk
  WHERE (o.hier IN ('무신사 풋웨어실','무신사 플레이어실') OR o.com_id IN ({EXTRA})
         OR (o.ord_com_type='공급(매입)' AND o.brand IN (SELECT brand_id FROM pur_brands)))
    AND COALESCE(o.com_id,'') NOT IN ({EXCLUDE})
    AND o.ord_state_date BETWEEN wk.p4s AND wk.w0e
  UNION SELECT brand_id FROM pur_brands
),
goods_scope AS (
  SELECT CAST(g.goods_no AS STRING) AS product_no, g.brand AS brand_id,
         ANY_VALUE(g.goods_nm) AS goods_nm, ANY_VALUE(g.brand_nm) AS brand_nm, ANY_VALUE(g.com_id) AS com_id
  FROM datamart.datamart.goods g
  WHERE g.brand IN (SELECT brand_id FROM brand_scope) AND g.goods_no IS NOT NULL
  GROUP BY CAST(g.goods_no AS STRING), g.brand
),
onl AS (
  SELECT o.brand AS brand_id, CAST(o.goods_no AS STRING) AS product_no,
    CASE WHEN o.ord_com_type='공급(매입)' THEN '매입' ELSE '위탁' END AS gubun,
    SUM(o.sell_sub_clm_qty) AS on_qty, SUM(o.normal_gmv) AS on_gmv, SUM(o.revenue) AS on_nt
  FROM team.sales.dsh_d_upt_orders_merged_yoy_new o CROSS JOIN wk
  WHERE o.brand IN (SELECT brand_id FROM brand_scope) AND o.goods_no IS NOT NULL
    AND COALESCE(o.com_id,'') NOT IN ({EXCLUDE})
    AND o.ord_state_date BETWEEN wk.w3s AND wk.w0e
  GROUP BY o.brand, CAST(o.goods_no AS STRING), CASE WHEN o.ord_com_type='공급(매입)' THEN '매입' ELSE '위탁' END
),
offl AS (
  SELECT ev.brand_id, CAST(ev.goods_no AS STRING) AS product_no,
    CASE WHEN ev.ord_com_type='공급(매입)' THEN '매입' ELSE '위탁' END AS gubun,
    SUM(ev.sell_qty-ev.clm_qty) AS off_qty, SUM(ev.ord_amt) AS off_gmv, SUM(ev.profit) AS off_nt
  FROM team.sales.dsh_d_upt_editorial_summary_v ev CROSS JOIN wk
  WHERE ev.brand_id IN (SELECT brand_id FROM brand_scope) AND ev.goods_no IS NOT NULL
    AND ev.ord_com_type IS NOT NULL
    AND ev.ord_state_date BETWEEN wk.w3s AND wk.w0e
  GROUP BY ev.brand_id, CAST(ev.goods_no AS STRING), CASE WHEN ev.ord_com_type='공급(매입)' THEN '매입' ELSE '위탁' END
),
stk AS (
  SELECT gs.brand_id, gs.product_no,
    CASE WHEN h.biz_type='1P' THEN '매입' ELSE '위탁' END AS gubun,
    SUM(h.end_stock_qty) AS total_cur,
    SUM(CASE WHEN ({PAIRS}) OR h.storage_loc_cd IN ({P1700}) THEN h.end_stock_qty ELSE 0 END) AS store_cur
  FROM team.sales.dsh_sku_stock_history_by_storage h
  JOIN goods_scope gs ON h.product_no=gs.product_no
  CROSS JOIN wk
  WHERE h.platform='MUSINSA' AND h.biz_type IN ('1P','3P','MFS')
    AND h.base_date=wk.w0
    AND COALESCE(h.com_id,'') NOT IN ({EXCLUDE})
  GROUP BY gs.brand_id, gs.product_no, CASE WHEN h.biz_type='1P' THEN '매입' ELSE '위탁' END
),
axis AS (
  SELECT brand_id, product_no, gubun FROM onl
  UNION SELECT brand_id, product_no, gubun FROM offl
  UNION SELECT brand_id, product_no, gubun FROM stk
)
SELECT
  CAST(a.brand_id AS STRING) AS brand_code, a.gubun,
  a.product_no, gs.goods_nm, gs.brand_nm,
  COALESCE(s.total_cur,0) AS total_cur, COALESCE(s.store_cur,0) AS store_cur,
  COALESCE(s.total_cur,0)-COALESCE(s.store_cur,0) AS logi_cur,
  COALESCE(o.on_qty,0) AS qty_on, COALESCE(f.off_qty,0) AS qty_off, COALESCE(o.on_qty,0)+COALESCE(f.off_qty,0) AS qty_tot,
  ROUND(COALESCE(o.on_gmv,0)) AS gmv_on, ROUND(COALESCE(f.off_gmv,0)) AS gmv_off, ROUND(COALESCE(o.on_gmv,0)+COALESCE(f.off_gmv,0)) AS gmv_tot,
  ROUND(COALESCE(o.on_nt,0)) AS nt_on, ROUND(COALESCE(f.off_nt,0)) AS nt_off, ROUND(COALESCE(o.on_nt,0)+COALESCE(f.off_nt,0)) AS nt_tot,
  ROUND((COALESCE(o.on_qty,0)+COALESCE(f.off_qty,0)) / NULLIF(COALESCE(o.on_qty,0)+COALESCE(f.off_qty,0)+COALESCE(s.total_cur,0),0)*100,1) AS sell_through,
  CASE WHEN (COALESCE(o.on_qty,0)+COALESCE(f.off_qty,0))>0 THEN ROUND(COALESCE(s.total_cur,0)/((COALESCE(o.on_qty,0)+COALESCE(f.off_qty,0))/28.0),1) END AS days_all
FROM axis a
LEFT JOIN onl o ON a.brand_id=o.brand_id AND a.product_no=o.product_no AND a.gubun=o.gubun
LEFT JOIN offl f ON a.brand_id=f.brand_id AND a.product_no=f.product_no AND a.gubun=f.gubun
LEFT JOIN stk s ON a.brand_id=s.brand_id AND a.product_no=s.product_no AND a.gubun=s.gubun
LEFT JOIN goods_scope gs ON a.brand_id=gs.brand_id AND a.product_no=gs.product_no
WHERE COALESCE(o.on_gmv,0)+COALESCE(f.off_gmv,0)>0 OR COALESCE(s.total_cur,0)>0
"""


def fetch_ips_goods() -> pd.DataFrame:
    """IPS 상품단위(드릴다운) 스냅샷. 브랜드 집계와 동일 스코프, goods_no 그레인.
       + PLANT 1000 입고예정(open PO)을 goods_no로 병합(매입 한정)."""
    p1700, purb = _ips_prefetch()
    d = run_df(_ips_goods_sql(p1700, purb))
    for c in ("brand_code", "gubun", "product_no", "goods_nm", "brand_nm"):
        d[c] = d[c].fillna("").astype(str)
    # 상품단위 입고예정(open PO) 병합.
    try:
        q = r"""
          SELECT CAST(S_MATNR AS STRING) AS product_no, SUM(CAST(MENGE AS DOUBLE)) AS inbound_po
          FROM datamart.erp.mmaws0090
          WHERE WERKS='1000' AND (LOEKZ IS NULL OR LOEKZ='')
            AND EINDT >= date_format(current_date(),'yyyyMMdd')
          GROUP BY CAST(S_MATNR AS STRING)
        """
        po = run_df(q)
        po_map = dict(zip(po["product_no"].astype(str), pd.to_numeric(po["inbound_po"], errors="coerce").fillna(0.0)))
        d["inbound_po"] = [po_map.get(p, 0.0) if g == "매입" else 0.0
                           for p, g in zip(d["product_no"], d["gubun"])]
    except Exception:
        d["inbound_po"] = 0.0
    num_cols = [c for c in d.columns if c not in ("brand_code", "gubun", "product_no", "goods_nm", "brand_nm")]
    for c in num_cols:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d


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
# (레거시 5원천 재고 로더는 2026-11-14 제거 — 통합 원장 team.scm.stock 으로 일원화. 아래 상수는 구 Streamlit app.py 참조용으로만 남김.)
HUB_COLS = ["MFS", "허브1000", "허브1700"]          # 허브합계·다운스트림 집계용(불변)
# 허브1000(plant1000)을 lgort로 분해: 2000=온라인창고 / 2020·2060=오프라인창고 / 2010=반품창고.
# 집계 '허브1000'(=세 파트 합)은 그대로 두고, 상세 3열을 '추가'만 함(재고 탭에서만 상세 표시).
HUB1000_PARTS = ["허브1000-온라인", "허브1000-오프라인", "허브1000-반품"]
HUB_ALL = HUB_COLS + HUB1000_PARTS                 # 재고 피벗의 모든 창고 컬럼(스토어 컬럼 판별시 제외용)


# ---------------------------------------------------------------------------
# [신규·경량] 통합 재고 원장 team.scm.stock (SLS·IVL, 2026-11 이관) — 매입+위탁, 매장(STORE)+센터(FC)가 한 테이블.
#   기존 5원천(editorial / sap_inventory / raw_erp_stock / mfs_stock_daily / scm_hub_goods_meta) 조인을 1쿼리로 대체하기 위한
#   2026-11-14 부터 **유일한** 재고 로더다(레거시 5원천 로더 제거). 매입·위탁 모두 이 원장에서 온다.
#   · grain = 거점(매장 or 센터) × goods_no.  team.scm.stock 은 SKU 단위라 barcode/옵션은 없음(→ team.scm.stock_detail).
#   · 매장 = namespace 'STORE', root_node_id(문자) == shop_no → dim_store 로 대시보드 매장만.
#   · 센터 = namespace 'FC' (여주/안성/이천… 20곳, root_node_id = 문자코드 NWH01 등).
#   · 보유 = total_quantity (= 레거시 editorial sellable_qty / raw_erp wqty), 실가용 = remaining_quantity (할당 제외).
#   · 매입/위탁 = goods_no → bizest.goods.com_id → partnerportal.company.margin_type (WONGA=매입, FEE=위탁).
#   ⚠️ sku→goods 매핑 대표 플래그 = is_rep_uid_option_code_musinsa (2026-11-12 열 이름 변경: 옛 is_rep_uid_musinsa 는 없음).
#      새 기준(SKU당 대표 uid·option_code 한 쌍)에선 SKU당 1행(2026-11-13 검증: 2,205,689 SKU, 중복 0).
#      옛 기준에선 1:N 이 남아 부풀었으므로(1,626 SKU) DISTINCT (sku_id, uid) 는 안전장치로 유지.
#   ⚠️ total_quantity 는 음수가 있다(예: 쇼핑백 goods 3781910 이 매장마다 −수천). total_qty=원장 그대로,
#      total_qty_pos=SKU×거점 단위로 음수를 0 처리한 합 — 어느 쪽을 화면에 쓸지는 소비처에서 고른다.
# ---------------------------------------------------------------------------
_STOCK_SKU_MAP = r"""
sku_map AS (
  SELECT DISTINCT sku_id, uid
  FROM team.scm.mst_sku_mapping_uid_option_code
  WHERE platform = 'MUSINSA' AND is_rep_uid_option_code_musinsa = TRUE
)
"""

STOCK_LONG_COLS = ["namespace", "node_id", "node_name", "shop_type", "goods_no", "business_type",
                   "total_qty", "total_qty_pos", "remaining_qty", "alloc_qty"]


def _stock_long_sql(fc_scope: str = "store_goods") -> str:
    """통합 원장 1쿼리. fc_scope: 'store_goods'=매장에 있는 상품의 센터재고만(기존 피벗 행 기준과 같은 취지) /
       'all'=센터 전 상품 / 'none'=매장만."""
    fc_filter = "AND m.uid IN (SELECT goods_no FROM st WHERE goods_no IS NOT NULL)" if fc_scope == "store_goods" else ""
    fc_union = "" if fc_scope == "none" else f"""
        UNION ALL
        SELECT 'FC' AS namespace, s.root_node_id AS node_id, MAX(s.node_name) AS node_name, 'FC' AS shop_type,
               m.uid AS goods_no,
               SUM(s.total_quantity) AS total_qty, SUM(GREATEST(s.total_quantity, 0)) AS total_qty_pos,
               SUM(s.remaining_quantity) AS remaining_qty,
               SUM(s.allocation_quantity + s.virtual_allocation_quantity) AS alloc_qty
        FROM team.scm.stock s
        LEFT JOIN sku_map m ON m.sku_id = s.sku_id
        WHERE s.namespace = 'FC' {fc_filter}
        GROUP BY s.root_node_id, m.uid"""
    return ("WITH " + DIM_STORE + "," + _STOCK_SKU_MAP + r""",
        st AS (
          SELECT 'STORE' AS namespace, CAST(ds.shop_no AS STRING) AS node_id, ds.store_name AS node_name,
                 ds.shop_type AS shop_type, m.uid AS goods_no,
                 SUM(s.total_quantity) AS total_qty, SUM(GREATEST(s.total_quantity, 0)) AS total_qty_pos,
                 SUM(s.remaining_quantity) AS remaining_qty,
                 SUM(s.allocation_quantity + s.virtual_allocation_quantity) AS alloc_qty
          FROM team.scm.stock s
          JOIN dim_store ds ON CAST(ds.shop_no AS STRING) = s.root_node_id   -- STORE root_node_id == shop_no
          LEFT JOIN sku_map m ON m.sku_id = s.sku_id                         -- 미매핑 SKU 유지(goods_no NULL)
          WHERE s.namespace = 'STORE'
          GROUP BY ds.shop_no, ds.store_name, ds.shop_type, m.uid),
        u AS (
          SELECT * FROM st""" + fc_union + r""")
        SELECT u.namespace, u.node_id, u.node_name, u.shop_type,
               CAST(COALESCE(u.goods_no, 0) AS BIGINT) AS goods_no,
               CASE c.margin_type WHEN 'WONGA' THEN '매입' WHEN 'FEE' THEN '위탁' ELSE '기타' END AS business_type,
               CAST(u.total_qty AS DOUBLE)     AS total_qty,
               CAST(u.total_qty_pos AS DOUBLE) AS total_qty_pos,
               CAST(u.remaining_qty AS DOUBLE) AS remaining_qty,
               CAST(u.alloc_qty AS DOUBLE)     AS alloc_qty
        FROM u
        LEFT JOIN musinsa.bizest.goods g          ON CAST(g.goods_no AS BIGINT) = u.goods_no
        LEFT JOIN musinsa.partnerportal.company c ON c.com_id = g.com_id
        WHERE u.total_qty <> 0 OR u.remaining_qty <> 0
    """)


def fetch_stock_long(fc_scope: str = "store_goods") -> pd.DataFrame:
    """[신규·경량] 통합 재고(매장+센터, 매입+위탁) 롱포맷 — 거점 × goods_no 당 1행, Databricks 1쿼리.
       열: namespace('STORE'|'FC') / node_id(매장=shop_no 문자, 센터=코드) / node_name(매장=dim_store store_name, 센터=센터명) /
           shop_type / goods_no(미매핑 SKU=0) / business_type(매입|위탁|기타) /
           total_qty(보유) / total_qty_pos(음수 SKU 0처리 보유) / remaining_qty(실가용) / alloc_qty(진할당+가할당).
       attrs['data_ts'] = 원장 최신 갱신시각(KST 문자열). 캐시 데코레이터 없음(스냅샷/대사 전용 — store 가 parquet 로 보관)."""
    df = run_df(_stock_long_sql(fc_scope))
    if df is None or df.empty:
        return pd.DataFrame(columns=STOCK_LONG_COLS)
    df["goods_no"] = pd.to_numeric(df["goods_no"], errors="coerce").fillna(0).astype("int64")
    for c in ("total_qty", "total_qty_pos", "remaining_qty", "alloc_qty"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in ("namespace", "node_id", "node_name", "shop_type", "business_type"):
        df[c] = df[c].fillna("").astype(str)
    df = df[STOCK_LONG_COLS]
    try:   # 원장 기준시각(매시 적재) — 데이터 기준 표기용
        ts = run_df("SELECT date_format(from_utc_timestamp(MAX(updated_at), 'Asia/Seoul'), 'yyyy-MM-dd HH:mm') t "
                    "FROM team.scm.stock WHERE namespace IN ('STORE','FC')").iloc[0, 0]
        df.attrs["data_ts"] = str(ts)
        df.attrs["data_date"] = str(ts)[:10]
    except Exception:
        pass
    return df


def stock_store_goods(long_df: pd.DataFrame, qty: str = "total_qty") -> pd.DataFrame:
    """fetch_stock_long 결과 → 매장 와이드(goods_no × 매장 열). 추가 조회 없음(pandas).
       qty = 'total_qty'(보유, 레거시 sellable 대응) | 'total_qty_pos' | 'remaining_qty'(실가용)."""
    st_ = long_df[long_df["namespace"] == "STORE"]
    if st_.empty:
        return pd.DataFrame(columns=["goods_no"])
    return (st_.pivot_table(index="goods_no", columns="node_name", values=qty, aggfunc="sum", fill_value=0.0)
            .rename_axis(columns=None).reset_index())


def stock_fc_goods(long_df: pd.DataFrame, qty: str = "total_qty") -> pd.DataFrame:
    """fetch_stock_long 결과 → 센터 와이드(goods_no × 센터명 열) + 'FC합계'. 추가 조회 없음(pandas)."""
    fc = long_df[long_df["namespace"] == "FC"]
    if fc.empty:
        return pd.DataFrame(columns=["goods_no", "FC합계"])
    w = fc.pivot_table(index="goods_no", columns="node_name", values=qty, aggfunc="sum", fill_value=0.0)
    w = w.rename_axis(columns=None)
    w["FC합계"] = w.sum(axis=1)
    return w.reset_index()


# ---------------------------------------------------------------------------
# [신규] 통합 원장 기반 재고 피벗 — load_inventory_pivot 과 같은 출력 계약(barcode × 매장 + 창고)으로,
#   수량 원천만 team.scm.stock_detail(매장·센터, 매입+위탁, 매시) 로 바꾼 것. (2026-11 사용자 결정)
#   · 매장 열      = stock_detail STORE **가용**(총 보유 − 할당; STORE_QTY_BASIS). 보유가 음수인 행은 원장 그대로.
#                    editorial/sap1700/SCM 오버레이 대체.
#   · 물류 열      = 센터(FC 20곳)를 **3열**로. 기존 MFS/허브1000/허브1700(+분해 3열) 대체.
#       총 물류 보유          = total_quantity (센터 실물 전체).
#       무신사 슬롯 재고(가용) = remaining_quantity(진할당·가할당 제외, 음수면 0) — 지금 판매·출고에 풀 수 있는 물량.
#       온라인 판매 제외       = online_except_quantity — 온라인에 안 풀고 묶어둔 물량(가할당 안에 포함).
#       (총 보유 − 가용 − 온라인 제외 = 진할당 + B2B 출고예정 등, 실측 FC 전체 약 23만.)
#       ⚠️ online_except 는 SKU×거점 단위(team.scm.stock)에만 있어 바코드로 **비례 배분**한다(SKU 내 바코드 보유 비중).
#   · 허브합계      = LOGI_SUM_COL = '무신사 슬롯 재고(가용)'(다운스트림 키 호환: daily/api 의 hub_total·hub, 재고보충 판정).
#   · 메타(옵션명·오프라인 카테고리·MD·업체/브랜드코드·사업구분)와 바코드→상품번호 귀속은 editorial 최신 스냅샷에서 가져온다
#     (신규 원장엔 없음. 상품번호는 오프라인 판매 번호와 맞아야 판매 조인이 유지됨).
#     editorial 에 없는 바코드는 상품마스터(이름·브랜드)·company.margin_type(사업구분)로 보강, 옵션/카테고리는 공란/미분류.
#   · 행 = 매장 또는 센터에 수량(≠0)이 있는 바코드. 센터 재고는 '오프라인 취급 바코드'(매장 원장 ∪ editorial)로 한정.
#   쿼리 2개(수량 long 3열 / 바코드 메타 — 상품마스터 조인은 서버에서) + 기존 보정 4개 + 기준시각 1개.
#   bizest.goods 700만 전량 수신·raw_erp·mfs·goods_meta·sap_inventory 스캔 없음. 수량 0 행은 내려받지 않는다.
# ---------------------------------------------------------------------------
LOGI_COL = "총 물류 보유"       # 물류 열 중 첫 열(센터 실물 전체)
# 매장 수량(점재고) 기준 — 사용자 결정(2026-11-13): 점재고는 전 지표에서 **가용** 으로 본다.
#   "available" = 총 보유 − 할당(원장 remaining_quantity). 매장의 할당은 실측상 거의 전부 반납 지시분이다.
#                 오래된 반납 지시로 할당이 보유보다 많으면 0, 보유 자체가 음수인 행(원장 음수)은 그 값 그대로.
#   "total"     = 총 보유(total_quantity) 그대로(이전 동작).
STORE_QTY_BASIS = "available"
# 물류 재고에서 제외할 센터(FC root_node_id). 비워두면 FC 20곳 전체.
#   후보(매장 출고 이력이 사실상 없는 센터): 'RTN01'(안성반품센터), 'NWH09'(여주글로벌센터) — 사용자 확정 후 여기만 바꾸면 된다.
LOGI_EXCLUDE_FC: tuple = ()
# 물류 재고 열 정의: {열 이름: team.scm.stock_detail(d) 수량식}. 첫 항목이 '허브합계'(창고 합계 키)가 된다.
#   열을 더 나누려면 한 줄 추가(invtab·파생 스냅샷·화면이 LOGI_COLS 를 따라 자동 반영). 예:
#     "물류 보유": "d.total_quantity"   # 가용+할당 합(총 보유)을 따로 보고 싶을 때
LOGI_AVAIL_COL = "무신사 슬롯 재고(가용)"   # 허브합계 키 — 판매·요약 탭의 '물류 가용', 재고보충 판정도 이 값
LOGI_OEX_COL = "온라인 판매 제외"
LOGI_MEASURES = {
    LOGI_COL:       "d.total_quantity",
    LOGI_AVAIL_COL: "GREATEST(d.remaining_quantity, 0)",
    # 온라인 판매 제외: SKU 단위 값을 그 SKU 안에서 바코드 보유 비중으로 배분(SKU 전체 보유가 0이면 0).
    LOGI_OEX_COL:   "CASE WHEN o.sku_tot > 0 THEN d.total_quantity * o.oex / o.sku_tot ELSE 0 END",
}
LOGI_SUM_COL = LOGI_AVAIL_COL
LOGI_COLS = list(LOGI_MEASURES)
# 이전 스냅샷의 단일 창고 열 이름 — 배포 직후 첫 갱신 전까지 캐시에 남아 있다.
#   매장 열로 오인되면 점재고가 물류 수량만큼 부풀므로 열 판별에는 항상 LOGI_ALL 을 쓴다.
LOGI_LEGACY_COLS = ["물류 재고", "물류 가용", "물류 할당(주문)"]
LOGI_ALL = LOGI_COLS + LOGI_LEGACY_COLS

_EDITORIAL_LATEST = r"""
el AS (   -- 파티션(ord_state_date_st, 'yyyyMMdd') 프루닝: 최근 10일 안에서 최신일
  SELECT MAX(ord_state_date_st) d FROM team.sales.dsh_d_upt_editorial_stock_summary
  WHERE ord_state_date_st >= date_format(date_sub(current_date(), 10), 'yyyyMMdd')
)
"""


def _unified_pivot_sql() -> tuple[str, str]:
    """통합 피벗용 SQL 2개(수량 long / 바코드 메타). 두 쿼리가 같은 CTE(lng)를 공유한다.
       · 수량 long  = (barcode, loc, qty) 3열만 — 수량 ≠ 0 인 행만 내려받는다.
       · 바코드 메타 = 수량이 있는 바코드만, editorial 메타 + 상품마스터(이름·브랜드·업체·사업구분)를 **서버에서** 조인해 1행으로.
         (예전엔 editorial 전 바코드 32만 행 + 상품명 12.6만 행을 따로 받아 pandas 로 맞췄다.)"""
    head = "WITH " + DIM_STORE + "," + _STOCK_SKU_MAP + "," + _EDITORIAL_LATEST
    # 센터(FC) 수량식들을 m0, m1… 로 한 번에 집계한 뒤 열 이름별 행으로 풀어 낸다(LOGI_MEASURES).
    fc_sums = ", ".join(f"SUM({expr}) AS m{i}" for i, expr in enumerate(LOGI_MEASURES.values()))
    fc_excl = ("" if not LOGI_EXCLUDE_FC else
               " AND d.root_node_id NOT IN (" + ",".join("'" + str(c).replace("'", "") + "'" for c in LOGI_EXCLUDE_FC) + ")")
    store_qty = ("CASE WHEN SUM(d.total_quantity) < 0 THEN SUM(d.total_quantity) ELSE GREATEST(SUM(d.remaining_quantity), 0) END"
                 if STORE_QTY_BASIS == "available" else "SUM(d.total_quantity)")
    fc_union = "".join(
        f"""
          UNION ALL
          SELECT 1 AS ord, barcode, '{name}' AS loc, sku_id, uid, CAST(m{i} AS DOUBLE) AS qty FROM fc WHERE m{i} <> 0"""
        for i, name in enumerate(LOGI_MEASURES))
    core = head + r""",
        sd AS (
          SELECT d.barcode, ds.store_name AS loc,
                 MAX_BY(d.sku_id, d.total_quantity) AS sku_id, MAX_BY(m.uid, d.total_quantity) AS uid,
                 """ + store_qty + r""" AS qty, SUM(d.total_quantity) AS tot
          FROM team.scm.stock_detail d
          JOIN dim_store ds ON CAST(ds.shop_no AS STRING) = d.root_node_id
          LEFT JOIN sku_map m ON m.sku_id = d.sku_id
          WHERE d.namespace = 'STORE' AND d.barcode IS NOT NULL AND d.barcode <> ''
          GROUP BY d.barcode, ds.store_name),
        spine AS (   -- 오프라인 취급 바코드 = 매장 원장 ∪ editorial 최신
          SELECT barcode FROM sd
          UNION
          SELECT s.barcode FROM team.sales.dsh_d_upt_editorial_stock_summary s
          JOIN el ON s.ord_state_date_st = el.d JOIN dim_store st ON st.shop_no = s.shop_no
          WHERE s.barcode IS NOT NULL),
        fc_oex AS (   -- 온라인 판매 제외(SKU×거점) → SKU 단위로 집계. 바코드 배분용 분모(sku_tot)도 함께.
          SELECT sku_id, SUM(online_except_quantity) AS oex, SUM(total_quantity) AS sku_tot
          FROM team.scm.stock WHERE namespace = 'FC' GROUP BY sku_id HAVING SUM(online_except_quantity) <> 0),
        fc AS (
          SELECT d.barcode,
                 MAX_BY(d.sku_id, d.total_quantity) AS sku_id, MAX_BY(m.uid, d.total_quantity) AS uid,
                 """ + fc_sums + r"""
          FROM team.scm.stock_detail d
          LEFT JOIN sku_map m ON m.sku_id = d.sku_id
          LEFT JOIN fc_oex o ON o.sku_id = d.sku_id
          WHERE d.namespace = 'FC' AND d.barcode IN (SELECT barcode FROM spine)""" + fc_excl + r"""
          GROUP BY d.barcode),
        lng AS (     -- 수량이 있는 (바코드 × 위치) 행만. 매장은 가용이 0 이어도 실물(총 보유)이 있으면 남긴다
                     --   (전량 반납 지시로 묶인 상품의 메타·입출고 행을 화면에서 잃지 않기 위함. 수량 값은 0)
          SELECT 0 AS ord, barcode, loc, sku_id, uid, CAST(qty AS DOUBLE) AS qty FROM sd WHERE qty <> 0 OR tot <> 0""" + fc_union + ")"
    q_qty = core + "\n        SELECT barcode, loc, qty FROM lng"
    q_meta = core + r""",
        bc AS (      -- 바코드별 대표 SKU·원장 uid: 매장 행 우선, 없으면 센터 행
          SELECT barcode,
                 COALESCE(MAX(CASE WHEN ord = 0 THEN sku_id END), MAX(CASE WHEN ord = 1 THEN sku_id END)) AS sku_id,
                 COALESCE(MAX(CASE WHEN ord = 0 THEN uid END),    MAX(CASE WHEN ord = 1 THEN uid END))    AS uid
          FROM lng GROUP BY barcode),
        em AS (      -- editorial 메타: 바코드당 1행(점재고가 가장 큰 오프라인 상품번호)
          SELECT * FROM (
            SELECT x.*, ROW_NUMBER() OVER (PARTITION BY x.barcode ORDER BY x.q DESC, x.goods_no DESC) AS rn
            FROM (
              SELECT s.barcode, CAST(s.goods_no AS BIGINT) AS goods_no, SUM(s.sellable_qty) AS q,
                     ANY_VALUE(s.goods_opt) AS goods_opt, ANY_VALUE(s.brand_nm) AS brand_nm, ANY_VALUE(s.goods_nm) AS goods_nm,
                     ANY_VALUE(s.final_large_nm_off) AS cat_top, ANY_VALUE(s.large_nm_off) AS cat_large,
                     ANY_VALUE(s.medium_nm_off) AS cat_medium, ANY_VALUE(s.offline_md_id) AS off_md_id,
                     ANY_VALUE(s.com_id) AS company_id, ANY_VALUE(s.brand) AS brand_id,
                     ANY_VALUE(CASE s.ord_com_type WHEN '입점(위탁)' THEN '위탁' WHEN '공급(매입)' THEN '매입' ELSE '기타' END) AS business_type
              FROM team.sales.dsh_d_upt_editorial_stock_summary s
              JOIN el ON s.ord_state_date_st = el.d
              JOIN dim_store st ON st.shop_no = s.shop_no
              WHERE s.barcode IS NOT NULL AND s.barcode IN (SELECT barcode FROM bc)
              GROUP BY s.barcode, CAST(s.goods_no AS BIGINT)) x)
          WHERE rn = 1)
        -- 상품번호 = editorial(오프라인 판매 번호) 우선 → 없으면 원장 대표 uid. 이름·브랜드는 상품마스터 우선(기존과 동일),
        -- 업체/브랜드코드·사업구분은 editorial 우선·마스터 보강.
        SELECT bc.barcode, bc.sku_id,
               COALESCE(em.goods_no, bc.uid)                    AS goods_no,
               em.goods_opt,
               COALESCE(NULLIF(b.brand_nm, ''), em.brand_nm)    AS brand_nm,
               COALESCE(NULLIF(gd.goods_nm, ''), em.goods_nm)   AS goods_nm,
               em.cat_top, em.cat_large, em.cat_medium, em.off_md_id,
               COALESCE(em.company_id, NULLIF(gd.com_id, ''))   AS company_id,
               COALESCE(em.brand_id, NULLIF(gd.brand, ''))      AS brand_id,
               COALESCE(em.business_type,
                        CASE WHEN gd.goods_no IS NULL THEN NULL
                             ELSE CASE c.margin_type WHEN 'WONGA' THEN '매입' WHEN 'FEE' THEN '위탁' ELSE '기타' END END) AS business_type
        FROM bc
        LEFT JOIN em ON em.barcode = bc.barcode
        LEFT JOIN musinsa.bizest.goods gd          ON CAST(gd.goods_no AS BIGINT) = COALESCE(em.goods_no, bc.uid)
        LEFT JOIN musinsa.partnerportal.brand b    ON b.brand = gd.brand
        LEFT JOIN musinsa.partnerportal.company c  ON c.com_id = gd.com_id
    """
    return q_qty, q_meta


@st.cache_data(ttl=900, show_spinner=False)
def load_inventory_pivot_unified() -> pd.DataFrame:
    """통합 원장 기반 재고 피벗(barcode × 매장 + '물류 재고'). load_inventory_pivot 대체용 — 출력 계약 동일:
       메타열 + 매장열 + 물류 열(LOGI_COLS) + 점재고합계 + 허브합계(=LOGI_COLS[0]) + sku_id + is_running + concept,
       attrs(store_cols, data_date, stock_basis)."""
    q_qty, q_meta = _unified_pivot_sql()
    qty = run_df(q_qty)
    meta = run_df(q_meta)
    meta_cols = ["goods_no", "goods_opt", "brand_nm", "goods_nm", "cat_top", "cat_large", "cat_medium",
                 "off_md_id", "company_id", "brand_id", "business_type"]
    qty["barcode"] = qty["barcode"].astype(str)
    qty["qty"] = pd.to_numeric(qty["qty"], errors="coerce").fillna(0.0)
    piv = qty.pivot_table(index="barcode", columns="loc", values="qty", aggfunc="sum", fill_value=0.0)
    piv = piv.rename_axis(columns=None)
    store_cols = [c for c in piv.columns if c not in LOGI_COLS]
    for lc in LOGI_COLS:
        if lc not in piv.columns:
            piv[lc] = 0.0
    piv = piv[store_cols + LOGI_COLS]

    # 바코드 메타(서버에서 조인 완료, 바코드당 1행 — 마스터 조인 중복 대비로 한 번 더 접는다) → 기본값만 채운다.
    meta["barcode"] = meta["barcode"].astype(str)
    m = meta.drop_duplicates("barcode").set_index("barcode").reindex(piv.index)
    m["goods_no"] = pd.to_numeric(m["goods_no"], errors="coerce").fillna(0).astype("int64")
    m["goods_nm"] = m["goods_nm"].fillna("")
    m["brand_nm"] = m["brand_nm"].fillna("(미상)")
    m["business_type"] = m["business_type"].fillna("매입")
    m["company_id"] = m["company_id"].fillna("").astype(str)
    m["brand_id"] = m["brand_id"].fillna("").astype(str)
    m["goods_opt"] = m["goods_opt"].fillna("")
    for col in ("cat_top", "cat_large", "cat_medium"):
        m[col] = m[col].fillna("미분류")
    m["off_md_id"] = m["off_md_id"].fillna("").astype(str)

    df = m[meta_cols].join(piv)
    df["점재고합계"] = df[store_cols].sum(axis=1)
    df["허브합계"] = df[LOGI_SUM_COL]                   # 창고 합계 키(호환) = 무신사 슬롯 재고(가용)
    df["sku_id"] = m["sku_id"].fillna("").astype(str)
    df = df.reset_index().rename(columns={"index": "barcode"})
    df = apply_category_override(df)
    df = apply_running_flag(df)
    df = apply_concept(df)
    for c in store_cols + LOGI_COLS + ["점재고합계", "허브합계"]:
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    df.attrs["store_cols"] = store_cols
    df.attrs["stock_basis"] = STORE_QTY_BASIS            # 점재고 기준(available=가용 / total=총 보유) — 화면 표기용
    try:   # 원장 기준시각(매시 적재, KST) — '재고 데이터' 표기용
        ts = run_df("SELECT date_format(from_utc_timestamp(MAX(updated_at), 'Asia/Seoul'), 'yyyy-MM-dd HH:mm') t "
                    "FROM team.scm.stock WHERE namespace IN ('STORE','FC')").iloc[0, 0]
        df.attrs["data_date"] = str(ts)
    except Exception:
        pass
    return df


def _inventory_goods_from(inv: pd.DataFrame) -> pd.DataFrame:
    """피벗 → 상품(goods_no) 단위 합산. load_inventory_goods 와 같은 열 구성(매장열 + 점재고합계 + 허브합계 + 창고열 + goods_nm)."""
    meta = {"barcode", "sku_id", "goods_no", "goods_opt", "brand_nm", "goods_nm", "business_type",
            "cat_top", "cat_large", "cat_medium", "off_md_id", "concept", "is_running",
            "company_id", "brand_id", "점재고합계", "허브합계", *LOGI_ALL, *HUB_ALL}
    store_cols = [c for c in inv.columns if c not in meta]
    hub = [c for c in (*LOGI_ALL, *HUB_COLS) if c in inv.columns]
    cols = store_cols + ["점재고합계", "허브합계"] + hub
    num = inv.groupby("goods_no")[cols].sum()
    nm = inv.groupby("goods_no")["goods_nm"].first()
    return num.join(nm).reset_index()


def load_inventory_goods_unified() -> pd.DataFrame:
    """통합 피벗의 상품 단위 합산(inventory_goods 스냅샷용)."""
    return _inventory_goods_from(load_inventory_pivot_unified())


def load_inventory_store_long_unified() -> pd.DataFrame:
    """(goods_no, store_name, 점재고) 롱포맷(inventory_store_long 스냅샷용) — 통합 피벗 기준."""
    g = load_inventory_goods_unified()
    store_cols = [c for c in g.columns
                  if c not in ({"goods_no", "goods_nm", "점재고합계", "허브합계", *LOGI_ALL} | set(HUB_ALL))]
    long = g.melt(id_vars="goods_no", value_vars=store_cols, var_name="store_name", value_name="점재고")
    # 수량 0 인 (상품×매장) 조합은 싣지 않는다 — 소비처가 전부 합계·조인이라 결과 동일, 행 수는 약 1/5.
    return long[long["점재고"] != 0].reset_index(drop=True)


# ---------------------------------------------------------------------------
# [신규] 매장 흐름(입고·출고 단계) — SCM-HUB 이동 문서(매입+위탁 모두) + 브랜드 직납. (2026-11 사용자 정의)
#   store_moves 스냅샷의 통합판: 기존 열(store_name/goods_no/option/in_qty/out_qty/hist_recv)에 단계 열을 '추가'한다.
#   매입 이동·매장 입고확정도 SCM-HUB 에 있으므로 ERP(oif313/erp315)는 쓰지 않는다 — 같이 쓰면 매입이 이중 집계된다.
#   (실측 2026-11-14, 최근 30일 SCM-HUB 이동 라인: 매입 447,054 / 위탁 187,290 — 매입이 오히려 많다. ERP 경로는 제거했다.)
#   [입고]  in_order   = 입고 지시  : 요청 − 센터 출고(아직 센터에서 안 나온 수량) + 브랜드 직납 미도착분
#           in_transit = 이동중     : 센터 출고 − 매장 입고확정 + 브랜드 직납 도착·확정 전
#           in_qty     = 입고 예정  = in_order + in_transit
#   [출고]  ret_pending = 반납 지시(출고 확정 전) : 요청 − 매장 출고 (아직 매장에 있는 수량)
#           out_qty     = 출고 예정 = 요청 − 물류 입고확정 = ret_pending + (매장 출고 후 물류 미입고)
#   (점재고(가용) = 총 보유 − 원장 할당은 재고 피벗의 매장 수량 자체가 담당한다 — STORE_QTY_BASIS.)
#   hist_recv = 그 매장 누적 입고확정(신규입고/필업 판정, 기존과 동일 의미).
#   미결 문서 = 상태 CREATED/PENDING/PARTIALLY_SHIPPED (CANCELED·FAILED·RECEIVED 제외). 오래 묵은 미결도 그대로 포함.
#   매장 매핑: dim_store → offline_shopno_storageid.storage_id(= SCM-HUB 스토리지) / 원장은 root_node_id == shop_no.
# ---------------------------------------------------------------------------
#   [지시일] in_order_at = 최근 입고 지시일  : 남은 입고(지시·이동중) 문서 라인의 생성일 중 가장 최근(KST)
#            ret_at      = 최근 반출 지시일  : 남은 반납·매장간 이동 라인의 생성일 중 가장 최근(KST)
#            (scm_hub 의 created_at 은 KST 벽시계로 적재됨 — 업무시간 9~18시 분포로 검증. 변환 불필요.)
STORE_FLOW_COLS = ["store_name", "goods_no", "option", "in_qty", "out_qty", "hist_recv",
                   "in_order", "in_transit", "ret_pending"]
STORE_FLOW_DATE_COLS = ["in_order_at", "ret_at"]
STORE_FLOW_ALL_COLS = STORE_FLOW_COLS + STORE_FLOW_DATE_COLS


def _store_flow_sql() -> str:
    return f"""
    WITH {DIM_STORE},
    sid AS (   -- 대시보드 매장 ↔ SCM-HUB 스토리지
        SELECT CAST(x.storage_id AS BIGINT) AS sid, ds.store_name, ds.shop_no
        FROM dim_store ds
        JOIN team.commercepm.offline_shopno_storageid x ON CAST(x.shop_no AS INT) = ds.shop_no),
    spo AS (SELECT fk_sku_id, fk_product_option_id FROM (
        SELECT fk_sku_id, fk_product_option_id, ROW_NUMBER() OVER (PARTITION BY fk_sku_id
          ORDER BY CASE WHEN mapping_type='ACTIVE' THEN 0 ELSE 1 END, updated_at DESC) rn
        FROM ocmp.scm_hub.sku_product_option) WHERE rn = 1),
    mv AS (    -- 취소·실패 제외 이동 품목(매입·위탁 모두)
        SELECT sm.fk_source_storage_id AS src, sm.fk_destination_storage_id AS dst,
               CAST(smi.fk_sku_id AS BIGINT) AS fk_sku_id,
               CASE WHEN smi.stock_movement_status IN ('CREATED','PENDING','PARTIALLY_SHIPPED') THEN 1 ELSE 0 END AS is_open,
               CAST(smi.requested_quantity AS DOUBLE) AS req, CAST(smi.shipped_quantity AS DOUBLE) AS shp,
               CAST(smi.received_quantity AS DOUBLE) AS rcv, smi.created_at AS ord_at
        FROM ocmp.scm_hub.stock_movement sm
        JOIN ocmp.scm_hub.stock_movement_item smi
          ON smi.fk_stock_movement_id = sm._id AND smi.stock_movement_status NOT IN ('CANCELED','FAILED')),
    inb AS (   -- 매장 입고(도착지 = 매장)
        SELECT s.store_name, mv.fk_sku_id,
               SUM(CASE WHEN mv.is_open = 1 THEN GREATEST(mv.req - mv.shp, 0) ELSE 0 END) AS in_order,
               SUM(CASE WHEN mv.is_open = 1 THEN GREATEST(mv.shp - mv.rcv, 0) ELSE 0 END) AS in_transit,
               MAX(CASE WHEN mv.is_open = 1 AND (mv.req > mv.shp OR mv.shp > mv.rcv) THEN mv.ord_at END) AS ord_at,
               SUM(mv.rcv) AS hist
        FROM mv JOIN sid s ON mv.dst = s.sid GROUP BY 1, 2),
    outb AS (  -- 매장 출고(출발지 = 매장): 반납 + 매장 간 이동
        SELECT s.store_name, mv.fk_sku_id,
               SUM(GREATEST(mv.req - mv.shp, 0)) AS ret_pending,
               MAX(CASE WHEN mv.req > mv.shp THEN mv.ord_at END) AS ret_at,
               SUM(GREATEST(mv.req - mv.rcv, 0)) AS outq
        FROM mv JOIN sid s ON mv.src = s.sid WHERE mv.is_open = 1 GROUP BY 1, 2),
    dir AS (   -- 브랜드 직납(센터 미경유): 도착 전 = 입고 지시, 도착·확정 전 = 이동중
        SELECT s.store_name, CAST(ii.fk_sku_id AS BIGINT) AS fk_sku_id,
               SUM(CASE WHEN ii.inbound_item_status = 'ARRIVED' THEN 0
                        ELSE GREATEST(COALESCE(ii.inbound_requested_quantity,0) - COALESCE(ii.inbound_completed_quantity,0), 0) END) AS in_order,
               SUM(CASE WHEN ii.inbound_item_status = 'ARRIVED'
                        THEN GREATEST(COALESCE(ii.inbound_requested_quantity,0) - COALESCE(ii.inbound_completed_quantity,0), 0) ELSE 0 END) AS in_transit,
               MAX(CASE WHEN COALESCE(ii.inbound_requested_quantity,0) > COALESCE(ii.inbound_completed_quantity,0)
                        THEN ii.created_at END) AS ord_at,
               SUM(COALESCE(ii.inbound_completed_quantity,0)) AS hist
        FROM ocmp.scm_hub.inbound ib
        JOIN ocmp.scm_hub.inbound_item ii ON ii.fk_inbound_id = ib._id AND ii.inbound_item_status <> 'CANCELLED'
        JOIN sid s ON ib.fk_destination_storage_id = s.sid
        WHERE ib.inbound_type = 'STORE_INBOUND' AND ii.fk_sku_id IS NOT NULL
        GROUP BY 1, 2),
    keys AS (SELECT store_name, fk_sku_id FROM inb UNION SELECT store_name, fk_sku_id FROM outb
             UNION SELECT store_name, fk_sku_id FROM dir),
    f AS (
        SELECT k.store_name, k.fk_sku_id,
               COALESCE(i.in_order, 0) + COALESCE(d.in_order, 0)     AS in_order,
               COALESCE(i.in_transit, 0) + COALESCE(d.in_transit, 0) AS in_transit,
               COALESCE(i.hist, 0) + COALESCE(d.hist, 0)             AS hist,
               GREATEST(i.ord_at, d.ord_at)                           AS ord_at,
               COALESCE(o.ret_pending, 0) AS ret_pending, COALESCE(o.outq, 0) AS outq, o.ret_at AS ret_at
        FROM keys k
        LEFT JOIN inb i  ON i.store_name = k.store_name AND i.fk_sku_id = k.fk_sku_id
        LEFT JOIN outb o ON o.store_name = k.store_name AND o.fk_sku_id = k.fk_sku_id
        LEFT JOIN dir d  ON d.store_name = k.store_name AND d.fk_sku_id = k.fk_sku_id)
    SELECT f.store_name, CAST(p.product_no AS STRING) AS goods_no, po.option_name AS option,
           SUM(f.in_order) AS in_order, SUM(f.in_transit) AS in_transit,
           SUM(f.in_order + f.in_transit) AS in_qty,
           SUM(f.ret_pending) AS ret_pending, SUM(f.outq) AS out_qty,
           SUM(f.hist) AS hist_recv,
           date_format(MAX(f.ord_at), 'yyyy-MM-dd') AS in_order_at,
           date_format(MAX(f.ret_at), 'yyyy-MM-dd') AS ret_at
    FROM f
    LEFT JOIN spo ON spo.fk_sku_id = f.fk_sku_id
    LEFT JOIN ocmp.scm_hub.product_option po ON po._id = spo.fk_product_option_id
    LEFT JOIN ocmp.scm_hub.product p ON p._id = po.fk_product_id
    WHERE p.product_no IS NOT NULL
      AND (f.in_order + f.in_transit > 0 OR f.outq > 0)
    GROUP BY f.store_name, CAST(p.product_no AS STRING), po.option_name
    """


def fetch_store_flow() -> pd.DataFrame:
    """매장 흐름(입고 지시·이동중 / 반납 지시·출고 예정) — store_name × goods_no × option.
       SCM-HUB(매입+위탁) 이동 문서 + 브랜드 직납. store_moves 스냅샷과 열 호환(in_qty/out_qty/hist_recv) + 단계 열.
       실패 시 빈 df(화면은 단계 열 없이 동작)."""
    try:
        df = run_df(_store_flow_sql())
    except Exception as e:
        import logging as _lg
        _lg.warning("store_flow 조회 실패: %s", str(e)[:300])
        return pd.DataFrame(columns=STORE_FLOW_ALL_COLS)
    if df is None or df.empty:
        return pd.DataFrame(columns=STORE_FLOW_ALL_COLS)
    df["goods_no"] = pd.to_numeric(df["goods_no"], errors="coerce").fillna(0).astype("int64")
    df["option"] = df["option"].fillna("").astype(str)
    for c in STORE_FLOW_COLS[3:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in STORE_FLOW_DATE_COLS:                       # 지시일(최근) — 'yyyy-MM-dd' 문자열, 없으면 공란
        df[c] = df[c].fillna("").astype(str) if c in df.columns else ""
    return df[STORE_FLOW_ALL_COLS]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_store_sku_status() -> dict:
    """대시보드 오프라인 매장 × SKU 전개 운영상태(SCM storage_sku.status) → {(store_name, sku_id): '운영중'|'미운영'}.
       OPERATION_ENABLED=운영중, 그 외(CREATED/OPERATION_DISABLED/DELETED)=미운영. storage_sku 행 없음 = '미지정'(맵에 없음).
       재고 탭 CSV 운영상태 열용. 라이브 + 1h 캐시(운영상태는 수시 변동)."""
    # ★ CSV엔 '재고 있는 (매장×SKU)'만 나오므로 그걸로 한정 — 전 storage_sku(4.5M)·인벤SKU(3.6M)는 과대(재고 없는 미운영 대량).
    #   stocked = **통합 원장(team.scm.stock) STORE 보유>0** 인 (매장, SKU). 2026-11 재고 원장 교체로 editorial 의존 제거.
    #   여기 등록(storage_sku)된 건만 운영중/미운영, 나머지(맵에 없음)=미지정.
    q = ("WITH " + DIM_STORE + r""",
        sid AS (SELECT CAST(s.storage_id AS BIGINT) AS storage_id, ds.store_name
                FROM team.commercepm.offline_shopno_storageid s
                JOIN dim_store ds ON ds.shop_no = CAST(s.shop_no AS INT)),
        stocked AS (
          SELECT DISTINCT ds.store_name AS store_name, CAST(k._id AS BIGINT) AS fk_sku_id
          FROM team.scm.stock s
          JOIN dim_store ds ON CAST(ds.shop_no AS STRING) = s.root_node_id
          JOIN ocmp.scm_hub.sku k ON k.sku_id = s.sku_id
          WHERE s.namespace = 'STORE' AND s.total_quantity > 0)
        SELECT sid.store_name AS store_name, CAST(sk.sku_id AS STRING) AS sku_id,
               MAX(CASE WHEN ss.status = 'OPERATION_ENABLED' THEN 1 ELSE 0 END) AS op
        FROM ocmp.scm_hub.storage_sku ss
        JOIN sid ON sid.storage_id = ss.fk_storage_id
        JOIN stocked k ON k.store_name = sid.store_name AND k.fk_sku_id = ss.fk_sku_id
        JOIN ocmp.scm_hub.sku sk ON sk._id = ss.fk_sku_id
        GROUP BY sid.store_name, CAST(sk.sku_id AS STRING)
    """)
    try:
        d = run_df(q)
    except Exception as e:
        import logging as _lg
        _lg.warning("storage_sku 운영상태 조회 실패(미지정 처리): %s", e)
        return {}
    out = {}
    for r in d.itertuples(index=False):
        sid = str(r.sku_id)
        if sid:
            out[(str(r.store_name), sid)] = "운영중" if int(r.op or 0) == 1 else "미운영"
    return out


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_store_code_map() -> dict:
    """오프라인 매장명 → SCM-HUB 스토어 코드(storage_id=ocmp.scm_hub.storage._id) {store_name: '130', ...}.
       재고 탭 CSV '스토어코드' 열용. 매장↔storage_id는 거의 불변이라 1일 캐시. 조회 실패 시 {}(공란)."""
    q = ("WITH " + DIM_STORE + r"""
        SELECT ds.store_name AS store_name, CAST(sid.storage_id AS STRING) AS store_code
        FROM dim_store ds
        JOIN team.commercepm.offline_shopno_storageid sid ON CAST(sid.shop_no AS INT) = ds.shop_no
        WHERE sid.storage_id IS NOT NULL
    """)
    try:
        d = run_df(q)
    except Exception as e:
        import logging as _lg
        _lg.warning("스토어코드(storage_id) 조회 실패(공란 처리): %s", e)
        return {}
    return {str(r.store_name): str(r.store_code) for r in d.itertuples(index=False) if str(r.store_code)}
