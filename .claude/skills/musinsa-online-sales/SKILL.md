---
name: musinsa-online-sales
description: >-
  Pull MUSINSA online (무신사닷컴) sales from Databricks — 순매출(GMV)·판매수량을 뽑는다.
  1차 소스는 사전집계 summary_goods_orders_daily_10(일별×상품, 취소·반품 차감된 순매출, CDC 무관·고속),
  상세는 order_group.order_opt(+order_mst) 직접집계. 무신사 국내 온라인만(글로벌 ZOZO/TMALL 제외).
  Use whenever the user asks 온라인 판매/매출/GMV/판매수량/일별매출 of a goods·브랜드·기간 (무신사닷컴).
  오프라인 매장판매는 musinsa-offline-sales, 온라인 '가격'은 musinsa-online-price, 상품속성은 musinsa-goods-master.
---

# 무신사 온라인 판매(order_group) — 조회 스킬

무신사닷컴 **온라인 실판매**(순매출·수량)를 뽑는 스킬. 오프라인 `musinsa-offline-sales`의 온라인 짝.
원천은 `musinsa.order_group` 스키마(온라인 주문 도메인, ~120테이블). **무신사 국내 온라인만** 대상(글로벌 ZOZOTOWN/TMALL 제외).

- 실행: Databricks MCP `mcp__databricks__execute_sql` (없으면 `ToolSearch "select:mcp__databricks__execute_sql"` 로 로드)
- **2계층 소스**:
  - **1차(권장) = `musinsa.order_group.summary_goods_orders_daily_10`** — 일별×goods×com_type 사전집계. 취소·반품 이미 차감된 **순매출**. CDC 아님(append-only), 빠름. 대부분의 "온라인 얼마 팔림" 질문은 여기서 끝.
  - **상세 = `musinsa.order_group.order_opt`** (+ `order_mst`) — 주문/옵션/시간대/앱·PC/쿠폰 등 드릴다운. current-state 미러(키 유니크, **dedup 불필요**).

## 핵심 정의 (반드시 지킴)
- **채널 필터**: 무신사 국내 온라인 = **`platform_code = 'MUSINSA'`** (글로벌 ZOZOTOWN·TMALL 제외). 전 주문 shop_no=1 / sale_place=HEAD_OFFICE.
  - ⚠️ 사전집계 `summary_goods_orders_daily_10`에는 platform 컬럼이 없지만 **이미 MUSINSA 국내만 담겨 있음**(글로벌 미포함, 2026-07-29 검증). 그래서 summary는 platform 필터 없이 그대로 온라인 국내 순매출.
- **주문상태 `ord_state`** (order_opt/order_mst): -20 결제오류 · -10 주문취소 · 1 입금대기 · 5 입금완료 · 10 결제완료 · 20 상품준비중 · 30 배송시작 · 35 배송중 · 40 배송완료 · 50 구매확정.
  - **판매/매출 인정 = `ord_state >= 10`(결제완료 이상)**, 취소(-10/-20)·입금대기(1) 제외. (⚠️ 오프라인 `order_status=50`과 정의 다름)
- **com_type / ord_com_type**: **1 = 매입(공급업체)**, **2 = 위탁(입점업체)**. 온라인은 위탁(2) 비중이 압도적.
- **CDC 주의**: order_group 테이블들은 `Op/tr_ts/tr_id` CDC 컬럼이 있으나 **키(ord_opt_no/ord_no)당 1행인 current-state 미러라 dedup 불필요**. deletes는 물리 제거됨.
- **GMV 정의**: 판매가 기준 = `price × qty` (order_opt) = summary의 `amt`(순)/`amt_10`(gross). 실결제액은 `pay_amount`(order_opt)/`recv_amt`(summary).

## 표준 쿼리 A — 일별 순매출 (1차 소스, 권장)

`summary_goods_orders_daily_10`. `ord_date`는 **INT yyyymmdd**. `amt`/`qty`는 **취소·반품 차감된 순값**.

```sql
SELECT
    ord_date,                                          -- INT yyyymmdd
    CASE com_type WHEN 1 THEN '매입' WHEN 2 THEN '위탁' ELSE '기타' END AS `구분`,
    CAST(SUM(amt)        AS BIGINT) AS `매출_GMV_순`,   -- 취소·반품 차감된 순 GMV(판매가 기준)
    CAST(SUM(qty)        AS BIGINT) AS `판매수량_순`,
    CAST(SUM(amt_10)     AS BIGINT) AS `GMV_gross`,     -- 결제완료 이상 gross (취소·반품 전)
    CAST(SUM(recv_amt)   AS BIGINT) AS `실수납_순`,
    CAST(SUM(sale_wonga) AS BIGINT) AS `원가`,
    CAST(SUM(sale_profit)AS BIGINT) AS `판매이익`
FROM musinsa.order_group.summary_goods_orders_daily_10
WHERE ord_date BETWEEN 20260701 AND 20260728        -- {{DATE}} INT yyyymmdd
  {{FILTER}}                                          -- 예: AND goods_no = 4841258 / AND com_type = 2
GROUP BY ord_date, com_type
ORDER BY ord_date, `구분`;
```
- **특정 상품 온라인 판매**: `{{FILTER}}` = `AND goods_no IN (4841258, ...)` (+ GROUP BY에 goods_no 추가)
- **전체 합계(기간)**: `GROUP BY` 및 ord_date SELECT 제거, 기간만 WHERE
- **매입/위탁 구분 불필요**: com_type을 SELECT/GROUP BY에서 빼면 합산
- 상품명·브랜드·카테고리는 `goods_no`로 `musinsa-goods-master` 조인해 보강

## 표준 쿼리 B — 상세 (order_opt, 주문·옵션·앱/PC 드릴다운)

`platform_code='MUSINSA' AND ord_state>=10` 고정. gross 기준(순매출은 반품/취소를 claim으로 차감하거나 summary 사용).

```sql
SELECT
    CAST(oo.ord_date AS DATE) AS sales_date,
    CAST(oo.goods_no AS BIGINT) AS goods_no,
    oo.goods_nm, oo.md_nm,
    CASE oo.ord_com_type WHEN 1 THEN '매입' WHEN 2 THEN '위탁' ELSE '기타' END AS `구분`,
    CAST(SUM(oo.qty)                       AS BIGINT) AS `판매수량`,
    CAST(SUM(CAST(oo.price AS BIGINT)*oo.qty) AS BIGINT) AS `매출_GMV`,   -- 판매가×수량(gross)
    CAST(SUM(oo.pay_amount)                AS DECIMAL(18,2)) AS `실결제액`
FROM musinsa.order_group.order_opt oo
WHERE oo.ord_date >= '2026-07-01' AND oo.ord_date < '2026-07-29'   -- {{DATE}} (timestamp)
  AND oo.platform_code = 'MUSINSA'
  AND oo.ord_state >= 10
  {{FILTER}}                                                        -- 예: AND oo.goods_no = 4841258
GROUP BY CAST(oo.ord_date AS DATE), oo.goods_no, oo.goods_nm, oo.md_nm, oo.ord_com_type
ORDER BY `매출_GMV` DESC
LIMIT 200;
```
- **앱/PC/모바일웹 구분**이 필요하면 `order_mst`를 `ord_no`로 조인: `app_yn='Y'`→앱, `app_yn='N' AND mobile_yn='Y'`→모바일웹, 둘 다 'N'→PC.
- **환불/취소 반영(순매출)**: `claim`(clm_type/ clm_state, `ord_opt_no` 귀속)로 차감하거나, 순값이 필요하면 **A(summary)** 사용을 우선.
- order_opt에는 `order_amount` 컬럼 없음(그건 order_mst) — 라인 금액은 `price*qty` 또는 `pay_amount`.

## 규칙
- 한글 컬럼 별칭 **백틱** 필수. `goods_no`는 `CAST(... AS BIGINT)`.
- summary의 `ord_date`는 **INT yyyymmdd**(예 20260729), order_opt/order_mst의 `ord_date`는 **timestamp**. 섞지 말 것.
- "순매출/취소·반품 반영" 질문 → A(summary, 이미 순값). "주문·시간대·앱PC·쿠폰 등 상세" → B(order_opt).
- 글로벌(ZOZOTOWN/TMALL)은 **제외**가 기본. 요청 시에만 order_opt에서 `platform_code` 조건 변경.
- 온라인 '판매가/세일가' 자체는 이 스킬 아님 → `musinsa-online-price`. 오프라인 매장판매 → `musinsa-offline-sales`.
- 대량 조회는 기간 필터 필수(order_opt는 4억행/2015~). summary는 일별 집계라 가벼움.
