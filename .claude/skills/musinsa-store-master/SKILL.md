---
name: musinsa-store-master
description: >-
  MUSINSA 오프라인 매장 마스터(매장코드 크로스워크) — 한 매장의 여러 시스템 코드를 이어준다:
  BIZEST shop_no ↔ SCM-HUB storage_id(위탁) ↔ ERP plant1000 lgort(매입) ↔ ERP plant1700 lgort.
  매장명도 함께. Use whenever the user needs 매장코드 매핑/변환 (shop_no ↔ SCM-HUB 창고코드 ↔
  ERP plant·lgort), 특정 매장의 시스템별 코드, 재고/이동 쿼리에 쓸 매장코드 확인, 오프라인 매장 목록.
  Databricks (team.commercepm.offline_shopno_storageid + ocmp.moss.shop_location) 기반.
---

# 무신사 오프라인 매장 마스터 (매장코드 크로스워크) — 조회 스킬

한 매장이 시스템마다 다른 코드를 쓴다. 이 스킬은 그 코드들을 **한 행으로 이어준다**:
**BIZEST `shop_no` ↔ SCM-HUB `storage_id`(위탁) ↔ ERP plant1000 `lgort`(매입) ↔ ERP plant1700 `lgort`** + 매장명.

- 실행: Databricks MCP `mcp__databricks__execute_sql` (없으면 `ToolSearch "select:mcp__databricks__execute_sql"` 로 로드)
- 원천:
  - `team.commercepm.offline_shopno_storageid` — `shop_no` ↔ `storage_id`(SCM-HUB 위탁 창고코드) ↔ `shop_name`
  - `ocmp.moss.shop_location` — `shop_no` ↔ `erp_plant_code`(1000=매입허브계열 / 1700) ↔ `erp_location_code`(=lgort) ↔ `erp_location_name`
- key = `shop_no` (BIZEST 오프라인 매장번호, dim_store와 동일 키). 1행 = 1매장.

## 반환 컬럼

| 컬럼 | 원천 | 뜻 |
|---|---|---|
| `shop_no` | offline_shopno_storageid.shop_no | BIZEST 매장번호 (공통 키) |
| `매장명` | .shop_name | 매장명 |
| `scmhub_storage_id` | .storage_id | SCM-HUB(위탁 MFS) 창고코드 |
| `erp_plant1000_lgort` | shop_location(plant=1000) | ERP 매입 plant1000 저장위치(lgort). 복수면 콤마결합 |
| `erp_plant1700_lgort` | shop_location(plant=1700) | ERP 매입 plant1700 lgort. 없는 매장은 NULL |

> ⚠️ 한 매장이 **한 플랜트에 복수 lgort**를 가질 수 있다(예: shop_no 82 → plant1700 `3044,3335`). 그래서 `collect_set`으로 모아 콤마결합. plant1700이 아예 없는 매장도 있음(NULL). 검증: shop_no **136**(킥스 스타필드 고양) → scmhub **197** / plant1000 **3058** / plant1700 **3053**.

## 표준 쿼리

```sql
SELECT
    s.shop_no,
    s.shop_name                                  AS `매장명`,
    s.storage_id                                 AS scmhub_storage_id,
    p1000.lgort                                  AS erp_plant1000_lgort,
    p1700.lgort                                  AS erp_plant1700_lgort
FROM team.commercepm.offline_shopno_storageid s
LEFT JOIN (
    SELECT shop_no, concat_ws(',', sort_array(collect_set(erp_location_code))) AS lgort
    FROM ocmp.moss.shop_location WHERE erp_plant_code = '1000' GROUP BY shop_no
) p1000 ON p1000.shop_no = s.shop_no
LEFT JOIN (
    SELECT shop_no, concat_ws(',', sort_array(collect_set(erp_location_code))) AS lgort
    FROM ocmp.moss.shop_location WHERE erp_plant_code = '1700' GROUP BY shop_no
) p1700 ON p1700.shop_no = s.shop_no
{{SHOP_FILTER}}
ORDER BY s.shop_no;
```

### 필터 (`{{SHOP_FILTER}}`)
- **전체(오프라인 매장 ~18개)**: 줄 삭제
- **단일 매장**: `WHERE s.shop_no = 136`
- **매장명 검색**: `WHERE s.shop_name LIKE '%고양%'`
- **특정 SCM-HUB/lgort 역조회**: `WHERE s.storage_id = 197` 또는 shop_location을 lgort로 필터 후 shop_no 확인

## 활용 (다른 스킬과 연결)
- **위탁(SCM-HUB) 재고/이동**: `scmhub_storage_id`를 `ocmp.scm_hub`·`mfs_stock_daily`·`selectshop_offline_shop_stock_v`의 창고코드로 사용.
- **매입(ERP) 재고/이동**: ERP 매장코드는 **(plant, lgort) 쌍**이다 — `erp_plant1000_lgort`/`erp_plant1700_lgort`를 `raw_erp_stock`·`pbo.moms.iif_sap_sto`(GR_LGORT) 등에 쓸 때 **plant와 함께** 필터(`CONCAT(WERKS,'-',GR_LGORT)`), lgort 단독 필터 금지(플랜트별 코드 재사용).
- 판매/재고/이동 스킬: `musinsa-offline-sales` / `musinsa-store-stock` / `erp-inventory-move-flow` · `scm-hub-inventory-move-flow` · `inventory-move-flow-all`.

## 규칙
- 한글 컬럼 별칭은 **백틱** 필수.
- `shop_no`는 offline_sales_mart_v / dim_store의 shop_no와 동일(대시보드 DIM_STORE 기준).
- lgort는 문자열(선행 0 가능) — 문자열로 다룬다. plant는 '1000'/'1700' 문자열.
