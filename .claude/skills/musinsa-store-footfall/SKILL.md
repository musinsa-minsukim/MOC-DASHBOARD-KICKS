---
name: musinsa-store-footfall
description: >-
  Pull MUSINSA offline-store 입객(방문자수·footfall) from Databricks — 매장 × 일자
  방문자수를 뽑는다 (원천 team.sales.retail_editorialshop_visitors_v, ADT 카운터 기반).
  shop_no 로 오프라인 매장 차원과 조인. Use whenever the user asks 매장별 입객/방문자수/
  footfall/유동인구/객수(입객) of a store·기간. ⚠️ 온라인 UV/PDP 트래픽(dsh_h_hst_traffic 등)과
  다름. 판매·GMV·외국인은 musinsa-offline-sales, 재고는 musinsa-store-stock, 대시보드는 musinsa-dashboard.
---

# 무신사 오프라인 매장 입객(방문자수) — 조회 스킬

오프라인 매장의 **입객수(방문자수, footfall)** 를 매장 × 일자로 뽑는 스킬.
판매(영수증)와 별개인 **매장 방문 카운터(ADT)** 데이터다.

- 실행: Databricks MCP `mcp__databricks__execute_sql` (없으면 `ToolSearch "select:mcp__databricks__execute_sql"` 로 로드)
- **원천(권장)**: `team.sales.retail_editorialshop_visitors_v`
  - 컬럼: `date`(INT yyyymmdd), `shop_no`(INT), `visitors`(STRING), `dt`(STRING yyyymmdd), **`visitors_int`(INT, 입객수)** ← 집계엔 `visitors_int` 사용.
- 매장 차원: `shop_no` 로 `team.sales.offline_sales_mart_v` (+ 이름 `team.commercepm.offline_shopno_storageid`) 와 조인. key=shop_no (shop_no=90→'RUN'). musinsa-offline-sales 의 `dim_store` CTE 재사용.

## 정의·주의 (반드시 인지)
- **입객 ≠ 영수증/구매건수.** 매장에 들어온 방문자 카운트다. 구매전환은 (영수증수 ÷ 입객수)로 별도 계산 — 영수증수는 musinsa-offline-sales 표준 쿼리 B.
- ⚠️ **온라인 트래픽 테이블과 혼동 금지**: `team.sales.dsh_h_hst_traffic`(온라인 UV), `team.sales.dsh_d_upt_traffic_sales_risingtf`(온라인 PDP/노출), `gspread.sales.store_traffic_daily_manual`(온라인 DAU/PDP) 은 **무신사닷컴 온라인 트래픽**이지 오프라인 입객이 아니다.
- **대체/원천 소스**(값 동일, 상황에 따라 사용):
  - `team.partner.offline_visitors_adt` — `dt`(DATE), `shop_no`(DOUBLE), `visitors`(LONG). ADT 카운터 원천.
  - `gspread.tech_pxa.offline_store_daily_visitors` — `date`(INT), `shop_no`(INT), `visitors`(INT). (갱신이 며칠 지연될 수 있음)
- 신선도: `team.sales` 버전이 가장 최신(예: 2026-07-29). 갱신 확인은 `SELECT MAX(dt) FROM team.sales.retail_editorialshop_visitors_v`.

## 표준 쿼리 — 매장별 입객 (기간 평균/합계)

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
v AS (
  SELECT shop_no, visitors_int, to_date(dt,'yyyyMMdd') AS d
  FROM team.sales.retail_editorialshop_visitors_v
)
SELECT st.store_name, st.shop_type,
       CAST(SUM(v.visitors_int) AS BIGINT) AS `입객_합계`,
       CAST(AVG(v.visitors_int) AS INT)    AS `일평균_입객`,
       COUNT(*)                             AS `일수`
FROM v
JOIN dim_store st ON st.shop_no = v.shop_no
WHERE v.d >= date_sub(current_date(), 30)      -- {{PERIOD}} 예: BETWEEN '2026-07-01' AND '2026-07-28'
GROUP BY st.store_name, st.shop_type
ORDER BY `일평균_입객` DESC;
```

- **일자별 추이**: `SELECT v.d, ... GROUP BY v.d` (매장 필터 `AND st.store_name='홍대'`).
- **특정 매장**: `WHERE ... AND st.store_name = '...'` 또는 `AND v.shop_no = 88`.
- **구매전환율**(입객 대비 영수증): 이 입객 합계와 musinsa-offline-sales 표준 쿼리 B의 `영수증수`를 매장·기간 맞춰 나눈다.

## 규칙
- 집계는 반드시 `visitors_int`(INT). `visitors`(STRING)는 그대로 SUM 금지.
- 날짜는 `dt`(STRING yyyymmdd)를 `to_date(dt,'yyyyMMdd')` 로 변환해 기간 필터. (`CAST(... AS DATE FORMAT ...)` 구문은 Databricks에서 미지원)
- 한글 컬럼 별칭은 **백틱** 필수.
- 큰 기간 전체보다 **기간 필터**를 붙인다. 입객 데이터 시작은 2023-03 전후.
