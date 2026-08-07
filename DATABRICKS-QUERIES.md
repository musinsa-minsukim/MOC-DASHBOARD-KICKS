# Databricks 쿼리 정리 (무신사 오프라인 대시보드)

모든 Databricks 쿼리는 **`db.py`** 에 모여 있다(유일한 원천 접속 모듈). 다른 모듈(`cmptab.py`,
`prodmeta.py`, `store.query`)의 SQL은 **DuckDB가 GCS의 parquet 캐시**를 조회하는 것이지 Databricks가 아니다.

- 접속: `db._new_connection()` — `databricks.sql.connect(..., use_cloud_fetch=False)` + `truststore`(회사 프록시 TLS).
  - 배포: 환경변수 `DATABRICKS_HOST/HTTP_PATH/TOKEN`. 로컬: `.streamlit/secrets.toml [databricks]`.
  - `use_cloud_fetch=False` 필수 — CloudFetch + truststore 충돌(`Cannot set verify_mode...`) 회피.
- 실행: `db.run_df(sql)` — 끊긴 세션 자동 재연결 1회 재시도(`_is_stale_session`). arrow→pandas.
- 워크스페이스/웨어하우스: `databricks-access` 메모리 참조 (host `musinsa-data-ws...`, warehouse `c0ee970a9c3ed562`).

> ⚠️ **검증된 핵심 수치 (절대 깨지면 안 됨):** 2026-06-21 총주문−환불 = **765,922,600원 / 20,649건**.
> 판매·영수증 쿼리를 바꿀 땐 이 수치 + 신/구 결과 대조를 반드시 통과시킬 것.

---

## 0. 공통 차원 — `DIM_STORE` (CTE 상수, [db.py:89](db.py))
오프라인 매장 15개 차원. 모든 fact 쿼리가 `JOIN dim_store`로 오프라인만 거른다.
- 소스: `team.sales.offline_sales_mart_v` (`shop_type IN ('selectshop','kicks','beauty','outlet')`)
  + 매장명 `team.commercepm.offline_shopno_storageid`
- 산출: `shop_no, shop_type(shop_no=90→'RUN'), store_name`. 키 = **shop_no**.

---

## 1. 판매 fact — `fetch_sales(since)` [db.py:121]
대시보드의 중심. **주문원장에서 직접** 판매를 집계(과거 전체 보유, offline_sales_master 뷰는 중복/과거누락이라 미사용).
- **소스:** `ocmp.moss.order_option` + `ocmp.moss.order_master` (주문) / `ocmp.moss.claim`(환불) /
  `team.sales.dsh_d_upt_editorial_stock_summary`(카테고리 catmap) / `musinsa.partnerportal.company_brand·brand·company`(브랜드·위탁매입).
  + 끝에 pandas로 `load_goods_master` 머지(상품명·브랜드명 보강).
- **모집단:** `dummy_order=0 AND order_status=50`(완료). 판매일 = `COALESCE(transaction_at, created_at)`.
- **환불:** `claim.claim_type='REFUND'`를 주문라인에 귀속, 환불처리일(`claim.created_at`) 기준, 부호 −1. (교환은 수량중립 제외)
- **외국인:** `tax_refund_type` 존재&≠'NONE' → `is_foreign=1` → `foreign_gmv`.
- **그레인:** 판매일 × 매장 × shop_type × 위탁매입 × 브랜드 × goods_no × 카테고리(top/large/medium) × off_md_id × company_id × brand_id.
- **지표:** `qty, gmv(=Σ order_amount), normal_amt, pay, foreign_gmv` (모두 부호 반영 SUM).
- **증분:** `since='YYYY-MM-DD'` 주면 그 날짜 이후만(store가 최근 45일 창으로 호출). `None`=전체.

## 2. 영수증/객단가 fact — `fetch_receipts(since)` [db.py:220]
객단가(AOV) 전용. **쇼핑백 등이 섞인 상품수량이 아니라 영수증(주문) 수로** 나눠야 정확.
- 소스/모집단: fetch_sales와 동일(`order_master`+`order_option`, dummy=0, status=50, 오프라인).
- 그레인: 판매일 × 매장 × shop_type × is_foreign.
- 지표: `receipts = COUNT(DISTINCT order_id)`, `gmv = SUM(order_amount)`. → 객단가 = gmv / receipts.

## 3. 최근 거래시각 — `sales_latest_ts()` [db.py:105]
`MAX(COALESCE(transaction_at, created_at))` (fetch_sales와 동일 모집단). 신선도 표시용.

## 4. 상품 마스터 — `load_goods_master()` [db.py:271]  ⭐최적화됨
goods_no → `goods_nm, brand_nm, reg_date, style_no, normal_price, sale_price`.
- 소스: `musinsa.bizest.goods` + `musinsa.partnerportal.brand`(브랜드명).
- **⭐ 오프라인 주문된 적 있는 goods_no만**(`sold` CTE: order_option⋈order_master⋈dim_store, dummy=0) INNER JOIN
  → 600만 → 약 11.5만 행. 판매 행 goods_no는 100% 이 집합에 포함되므로 enrichment 동일.
  (검증 2026-06-28: 최근30일 판매 goods 누락 0, 속성 불일치 0) → scale-to-zero 가능케 한 핵심.
- `reg_date`=상품 등록일(신규 판정 기준), `sale_price=bizest.goods.price`(0=할인없음→정상가).

## 5. 컨셉 맵 — `load_concept_map()` [db.py:258]
(company_id, brand_id) → concept. 소스 `team.sales.dsh_d_upt_editorial_summary_v`. 뷰 불안정 → try/except(실패시 빈 맵).

## 6. MD 한글명 — `load_md_names()` [db.py:310]
md_id → 한글명. 소스 3곳 UNION(editorial_stock_summary 최신 + partnerportal.company + bizest.goods), md_id별 최빈값.

## 7. 재고 스냅샷 — `load_inventory()` [db.py:329]
매장 재고 요약(판매가능/가용 수량). 소스 `team.sales.dsh_d_upt_editorial_stock_summary` **최신 ord_state_date**.
- 위탁매입: `ord_com_type` '입점(위탁)'→위탁 / '공급(매입)'→매입. 그레인: 위탁매입×매장×shop_type×브랜드×goods_no.

## 8. 고객/외국인 — `load_customer()` [db.py:360]
소스 `team.sales.dsh_d_upt_editorial_summary_customer` (`summary_period_unit='daily_total'`).
- 그레인: 일자×매장×shop_type×성별×연령대×회원여부. 지표: gmv, normal_gmv, foreign_gmv(tax), qty, buyer, foreign_buyer.

## 9. 재고 피벗 — `load_inventory_pivot()` [db.py:395]  (가장 복잡)
바코드(상품·옵션) × 매장 + 허브 재고 피벗. 전산 정의:
- **위탁 = MFS**(SCM-HUB), **매입 = 허브1000**(plant1000 창고 `2000·2010·2020·2060`) **+ 허브1700**(plant1700 `2000`) **+ 오프라인 매장재고**.
- 소스(5종):
  - 매장재고: `team.sales.dsh_d_upt_editorial_stock_summary`(최신) — barcode별 매장 sellable_qty.
  - 플랜트1700 매장재고: `ocmp.moss.sap_inventory` ⋈ `ocmp.moss.shop_location`(plant/location→shop_no) ⋈ dim_store.
  - 허브1000/1700: `team.partner.raw_erp_stock`(최신 dt, store_cd/lgort 필터).
  - MFS(위탁): `team.commercepm.mfs_stock_daily` ⋈ `team.scm.scm_hub_goods_meta`(sku_id→supplier_barcode).
  - 허브 메타(이름): `team.scm.scm_hub_goods_meta`.
- 행 기준 = 매장 ∪ 허브1700 ∪ MFS 바코드(허브1000 단독은 값만 유지, 행 제외).
- ⚠️ **이름 보강 slim 쿼리([db.py:525])는 아직 `bizest.goods` 600만 행(3컬럼) 전체 조회** — 재고는 미판매 상품도
  있어 `load_goods_master`의 '판매 goods' 필터를 그대로 못 씀. 현재 4Gi에서 7시 full 102.9초로 OK이나,
  **향후 최적화 후보**: 재고 바코드의 goods_no 집합으로 서브쿼리 필터.

## 10. 파생(쿼리 없음, pandas) — `load_inventory_goods()` [db.py:559] / `load_inventory_store_long()` [db.py:573]
피벗을 goods_no 단위 합산 / (goods_no×매장) 롱포맷으로 변환. Databricks 재조회 없음.

---

## 갱신 매핑 (어떤 쿼리가 언제 도는가)
`store.refresh_*` → 위 함수 호출 → 결과를 GCS `*.parquet`로 원자적 교체.

| 캐시(parquet) | 함수 | 갱신 주기 |
|---|---|---|
| `sales`, `receipts` | fetch_sales / fetch_receipts (증분 45일) | **매시** 11:10~23:10 + 07:00 |
| `goods_master`, `inventory_pivot`, `inventory_goods`, `inventory_store_long`, `customer` | 스냅샷 전체 교체 | **매일 07:00** (mode=full) |

(객단가용 `receipts`는 판매와 함께 갱신. `concept_map`·`md_names`는 app 단 단기 캐시.)

---

## 쿼리 변경 시 검증 절차 (필수)
1. 후보 쿼리를 로컬에서 실행: `.venv\Scripts\python.exe` + `from db import run_df, DIM_STORE`.
2. 신/구 결과 대조 — 행수, `SUM(gmv)`, `SUM(qty)`, 그레인별 합계, **검증수치(765,922,600/20,649)**.
3. 커버리지(머지 손실 없는지) + 속성 동등성 표본 비교. 전부 일치해야 적용.
4. 통과 후에만 db.py 수정 → `python -c "import api"` → 프론트 `npm run build` → 재배포.
(예시: 2026-06-28 goods_master 최적화 검증 스크립트 패턴 참조.)
