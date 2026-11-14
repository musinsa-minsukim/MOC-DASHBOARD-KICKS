---
name: musinsa-offline-sales
description: >-
  Pull MUSINSA offline-store (MOSS) sales from Databricks — 순판매수량·매출(GMV)을 주문원장에서
  직접 집계(주문−환불)해 기간·매장·상품·브랜드별로 뽑고, 영수증수/객단가(AOV)도 지원한다
  (원천 ocmp.moss.order_master + order_option + claim). 글로벌 고객의 국가별(국적) GMV·비중도
  ocmp.moss.tax_free_customer(면세환급 국적) 조인으로 지원. Use whenever the user asks 오프라인 판매/
  매출/판매수량/GMV/영수증/객단가/외국인·글로벌 고객·국가별(국적) 매출 of a store·goods·brand·기간 (MOSS 실판매).
  재고는 musinsa-store-stock, 상품속성은 musinsa-goods-master, 대시보드 전체는 musinsa-dashboard.
---

# 무신사 오프라인 판매(MOSS) — 조회 스킬

오프라인 15개 매장(MOSS)의 **실판매**를 주문원장에서 직접 집계하는 스킬.
대시보드 `fetch_sales`/`fetch_receipts`와 **동일 로직·동일 검증값**을 파라미터 쿼리로 뽑는다.

- 실행: Databricks MCP `mcp__databricks__execute_sql` (없으면 `ToolSearch "select:mcp__databricks__execute_sql"` 로 로드)
- 원천: `ocmp.moss.order_option` + `ocmp.moss.order_master` (주문), `ocmp.moss.claim` (환불)
- 매장 차원: `team.sales.offline_sales_mart_v` (shop_type in selectshop/kicks/beauty/outlet) + 이름 `team.commercepm.offline_shopno_storageid`. key=shop_no (shop_no=90→'RUN').

## 핵심 정의 (반드시 지킴)
- **모집단**: `dummy_order = 0 AND order_status = 50` (더미 제외, 유효완료 주문).
- **판매일**: `COALESCE(transaction_at, created_at)` 의 DATE (거래시각 없으면 생성시각).
- **순판매 = 주문(+1) − 환불(−1)**. 환불 = `claim.claim_type='REFUND'`, 환불처리일(`claim.created_at`) 기준. **교환은 수량중립이라 제외.**
- **검증 불변식(절대 깨지면 안 됨)**: `2026-06-21` 순매출 = **765,922,600 KRW**, **순판매수량 = 20,649개**.
  판매/영수증 로직 바꾸면 이 값으로 검증. 단 아래 두 가지를 꼭 지켜야 값이 맞는다:
  - **두 번째 값은 영수증수가 아니라 순판매수량**(`SUM(sgn*quantity)`)이다. 같은 날 영수증수는 **9,366건**(객단가 81,777원).
  - **매장 필터(`dim_store` 조인)를 반드시 걸고, `shop_no` 19(무신사 뷰티 스페이스 1)는 제외**한다.
    19번은 불변식 기록 이후 `offline_sales_mart_v` 에 추가된 매장이라, 넣으면 766,039,700 / 20,658 이 된다.
    필터를 아예 빼면 MOSS 전체(무신사 스탠다드·29CM 포함)가 잡혀 2,222,505,896 / 36,034 가 나온다.
  - 실행 가능한 검증 쿼리: `tools/verify_moss.sql` (`python tools/dbx.py tools/verify_moss.sql`).
- **객단가(AOV)** = `SUM(order_amount)`(완료주문, 환불 미반영) / `COUNT(DISTINCT order_id)`. 즉 분자는 영수증 시점 gross, 환불은 순매출(GMV)에만 반영.
- **수베니어샵(쇼핑백)** 은 대시보드에서 순판매수량 0 처리(매출은 유지). 필요 시 브랜드명='수베니어샵' 별도 취급.

## 표준 쿼리 A — 순매출 (판매수량·GMV, 환불 반영)

`{{DIM}}`(집계 축)과 `{{ORD_DATE}}`/`{{REF_DATE}}`(기간), `{{FILTER}}`만 바꿔 쓴다.

```sql
WITH dim_store AS (
  SELECT t.shop_no,
    CASE WHEN t.shop_no = 90 THEN 'RUN' ELSE UPPER(t.shop_type) END AS shop_type,
    REGEXP_REPLACE(TRIM(COALESCE(sid.shop_name, t.shop_nm)), ' +', ' ') AS store_name
  FROM (SELECT DISTINCT CAST(shop_no AS INT) AS shop_no, shop_type, shop_nm
        FROM team.sales.offline_sales_mart_v
        WHERE shop_type IN ('selectshop','kicks','beauty','outlet')) t
  LEFT JOIN team.commercepm.offline_shopno_storageid sid ON CAST(sid.shop_no AS INT) = t.shop_no
),
ord AS (   -- 주문 (+1)
  SELECT CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) AS sales_date,
         om.shop_no, CAST(oo.goods_no AS BIGINT) AS goods_no,
         1 AS sgn, oo.quantity, oo.order_amount
  FROM ocmp.moss.order_option oo
  JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
  WHERE om.dummy_order = 0 AND om.order_status = 50
    {{ORD_DATE}}          -- 예: AND CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) BETWEEN '2026-07-01' AND '2026-07-28'
),
refclaim AS (
  SELECT order_id, CAST(created_at AS DATE) AS refund_date
  FROM ocmp.moss.claim
  WHERE claim_type = 'REFUND'
    {{REF_DATE}}          -- 예: AND CAST(created_at AS DATE) BETWEEN '2026-07-01' AND '2026-07-28'
  GROUP BY order_id, CAST(created_at AS DATE)
),
ref AS (   -- 환불 (−1)
  SELECT rc.refund_date AS sales_date, om.shop_no, CAST(oo.goods_no AS BIGINT) AS goods_no,
         -1 AS sgn, oo.quantity, oo.order_amount
  FROM refclaim rc
  JOIN ocmp.moss.order_master om ON om.order_id = rc.order_id
  JOIN ocmp.moss.order_option oo ON oo.order_id = rc.order_id
  WHERE om.dummy_order = 0
),
lines AS (SELECT * FROM ord UNION ALL SELECT * FROM ref)
SELECT
    {{DIM}}                                             -- 예: st.store_name  또는  l.goods_no  또는 생략(전체합계)
    CAST(SUM(l.sgn * l.quantity)     AS BIGINT) AS `판매수량`,
    CAST(SUM(l.sgn * l.order_amount) AS BIGINT) AS `매출_GMV`
FROM lines l
JOIN dim_store st ON st.shop_no = l.shop_no
WHERE 1=1
  {{FILTER}}                                            -- 예: AND l.goods_no = 4841258
GROUP BY {{DIM_GROUP}}                                  -- {{DIM}}과 짝. 전체합계면 GROUP BY 절 삭제
ORDER BY `매출_GMV` DESC;
```

- **매장별**: `{{DIM}}` = `st.store_name,` / `{{DIM_GROUP}}` = `st.store_name`
- **상품별**: `{{DIM}}` = `l.goods_no,` / `{{DIM_GROUP}}` = `l.goods_no` (상품명은 `musinsa-goods-master`로 조인)
- **일자별**: `{{DIM}}` = `l.sales_date,` / `{{DIM_GROUP}}` = `l.sales_date`
- **전체합계**: `{{DIM}}` 비우고 `GROUP BY` 줄 삭제
- `{{FILTER}}` 예: 특정매장 `AND st.store_name = '홍대'`, 특정상품 `AND l.goods_no IN (...)`

## 표준 쿼리 B — 영수증수 · 객단가(AOV)

환불 미반영, 완료주문 기준. 분모 = `COUNT(DISTINCT order_id)`.

```sql
WITH dim_store AS ( /* 위와 동일 */ ),
ord AS (
  SELECT om.order_id,
         CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) AS sales_date,
         om.shop_no, oo.order_amount
  FROM ocmp.moss.order_option oo
  JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
  WHERE om.dummy_order = 0 AND om.order_status = 50
    {{ORD_DATE}}
)
SELECT st.store_name,
       CAST(SUM(o.order_amount)        AS BIGINT) AS `매출_gross`,
       COUNT(DISTINCT o.order_id)                 AS `영수증수`,
       CAST(SUM(o.order_amount) / COUNT(DISTINCT o.order_id) AS BIGINT) AS `객단가`
FROM ord o
JOIN dim_store st ON st.shop_no = o.shop_no
GROUP BY st.store_name
ORDER BY `객단가` DESC;
```
> ⚠️ 객단가 분모는 반드시 `COUNT(DISTINCT order_id)`. 한 주문이 여러 옵션/상품 행으로 쪼개져도 영수증은 1건.

## 표준 쿼리 C — 국가별(글로벌 고객) GMV

**글로벌 고객의 국적별 매출**은 면세환급 신청 테이블 `ocmp.moss.tax_free_customer` 에서만 나온다.
- 컬럼: `order_id`(→ order_master 조인 키, STRING), `nationality`(**국적 ISO3**: CHN/JPN/TWN/USA…), `birth_year`, `sex`, `created_at`.
- 조인: `tax_free_customer.order_id = CAST(order_master.order_id AS STRING)` → `order_master.shop_no`(매장) + `order_option.order_amount`(GMV). 모집단은 A/B와 동일(`dummy_order=0 AND order_status=50`).

### ⚠️ 정의·범위 (반드시 인지)
- **"글로벌 고객" = 면세환급을 신청(국적 기입)한 외국인.** 대시보드/쿼리 A·B의 외국인(`tax_refund_type<>'NONE'`)과 **개념은 같지만 범위가 좁다** — 검증(최근 90일 gross) 결과 `tax_free_customer` 외국인 GMV = `tax_refund_type` 외국인 GMV의 **약 85%**(0.853). 국적 분해가 필요할 때만 이 테이블을 쓰고, "전체 외국인 GMV"는 쿼리 A의 `foreign_gmv`(tax_refund_type)를 쓴다.
- 이 쿼리의 GMV는 **완료주문 gross(환불 미반영)**. 국가별 환불 귀속은 불가하므로 순매출(주문−환불)과 섞지 말 것. 국가별 비중이 목적이면 gross로 내부 정합(Σ국가 = 외국인)을 맞추는 게 낫다.

```sql
WITH dim_store AS ( /* 표준 쿼리 A와 동일 */ ),
ctry AS (   -- 매장 x 국적 GMV (면세신청 고객, gross)
  SELECT om.shop_no, tfc.nationality,
         CAST(SUM(oo.order_amount) AS BIGINT) AS gmv,
         COUNT(DISTINCT om.order_id)          AS orders
  FROM ocmp.moss.tax_free_customer tfc
  JOIN ocmp.moss.order_master om ON CAST(om.order_id AS STRING) = tfc.order_id
  JOIN ocmp.moss.order_option oo ON oo.order_id = om.order_id
  WHERE om.dummy_order = 0 AND om.order_status = 50
    {{ORD_DATE}}       -- 예: AND CAST(COALESCE(om.transaction_at,om.created_at) AS DATE) >= date_sub(current_date(),90)
  GROUP BY om.shop_no, tfc.nationality
),
forn AS ( SELECT shop_no, SUM(gmv) AS foreign_gmv FROM ctry GROUP BY shop_no )
SELECT st.store_name, st.shop_type, c.nationality,
       c.gmv                                   AS `국가별_GMV`,
       ROUND(c.gmv / f.foreign_gmv, 4)         AS `국가별_비중`,   -- 매장 외국인 GMV 내 비중(Σ=1)
       c.orders                                AS `주문수`
FROM ctry c
JOIN dim_store st ON st.shop_no = c.shop_no
JOIN forn f       ON f.shop_no  = c.shop_no
WHERE 1=1
  {{FILTER}}          -- 예: AND st.store_name='홍대'  /  AND c.nationality IN ('CHN','JPN','TWN')
ORDER BY st.store_name, c.gmv DESC;
```

- **국가 전체순위(매장 무관)**: `dim_store` 조인·`shop_no` 그룹 빼고 `GROUP BY nationality`.
- **매장별 외국인/내국인/전체 요약**과 국가 분해를 한 표에 합치는 예시는 구글시트 `오프라인 주요 지표` 생성 로직 참고(스크래치패드 `build_sheet.py`).

## 규칙
- 한글 컬럼 별칭 **백틱** 필수. `goods_no`는 `CAST(... AS BIGINT)`.
- 브랜드/사업구분(매입·위탁)·카테고리별 집계가 필요하면 `fetch_sales`의 dim_brand(`partnerportal.company_brand·brand·company`, margin_type FEE=위탁/WONGA=매입)와 catmap(`team.sales.dsh_d_upt_editorial_stock_summary` 최신 스냅샷)을 조인 — 상세는 대시보드 `db.py fetch_sales` 참고.
- 큰 기간 전체 조회는 무거움 → 기본적으로 **기간 필터를 붙인다**(예: 최근 N일). 전체 히스토리는 2023-10+.
- 순매출과 객단가의 매출은 정의가 다르다(순=주문−환불 / 객단가분자=완료주문 gross). 섞지 말 것.
