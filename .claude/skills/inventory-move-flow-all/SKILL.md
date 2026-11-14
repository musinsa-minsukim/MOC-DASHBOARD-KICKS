---
name: inventory-move-flow-all
description: >-
  매입·위탁 통합 재고이동플로우 — 한 매장의 매입(ERP/SAP)과 위탁(SCM-HUB) 재고이동을
  한 표에 합쳐 goods_no × 사이즈별 출고확정前 / 출고확정 / 이동중 / 매장입고확정 4단계를
  구분(매입/위탁/위탁출고/직납) 컬럼과 함께 보여준다. 매장 출고(나가는 수량 음수)·직납
  (브랜드→매장 직접 입고)도 포함한다. 사용자가 특정 매장의 전체 이동 현황(매입+위탁 통합),
  매입/위탁 합산·비교, 매장 입고 예정/확정을 물으면 사용한다. 개별 버전은
  erp-inventory-move-flow(매입) / scm-hub-inventory-move-flow(위탁). Databricks 기반.
---

# 매입·위탁 통합 재고이동플로우

한 매장으로의 **매입(ERP/SAP)** 과 **위탁(SCM-HUB)** 이동을 하나의 표로 합친다.
각 flow는 개별 스킬(`erp-inventory-move-flow`, `scm-hub-inventory-move-flow`)과 동일 로직이며, 여기서는 `구분`(매입/위탁) 컬럼을 붙여 `UNION ALL` 한다.
4단계 컬럼 순서는 동일: **출고확정前 / 출고확정 / 이동중 / 매장입고확정**. (매입/위탁 상품은 goods_no가 겹치지 않으므로 재집계 없이 스택.)

- 실행: Databricks MCP `mcp__databricks__execute_sql`.

## 매장 코드 2종 (필수)

매입과 위탁은 코드 체계가 달라 **둘 다** 지정해야 한다.

| 구분 | 파라미터 | 소스/조회 |
|---|---|---|
| 매입(ERP) | `{{ERP_PAIRS}}` = **(플랜트-lgort) 쌍**, `{{ERP_LGORTS}}` = lgort 목록 | `SELECT WERKS, GR_LGORT, GR_LGOBE, COUNT(*) FROM pbo.moms.iif_sap_sto WHERE GR_LGOBE LIKE '%매장명%' GROUP BY 1,2,3` |
| 위탁(SCM) | `{{SCM_STORAGE}}` (storage._id) | `SELECT _id, name FROM ocmp.scm_hub.storage WHERE name LIKE '%매장명%'` |

★★ **ERP lgort는 플랜트별이라 단독 필터 금지** — `3360`은 플랜트 **1000이면 "무신사 스토어 성수"(66,918라인)**, **1700이면 "스타필드_고양"**. 반드시 `CONCAT(WERKS,'-',GR_LGORT) IN (...)` 쌍으로.
★★ **한 매장이 플랜트마다 다른 lgort** → **1000·1700 둘 다** 넣어야 누락 없음(고양 킥스: 1000-3058 + 1700-3053, 3058만 쓰면 139개 누락).
한 플랜트 안에서도 lgort가 여러 개일 수 있음(킥스 성수 = 3048, 3057).

**📋 매장 코드 매핑표: `store-codes.md`** (이 스킬 폴더) — 34개 매장 ERP 1000/1700 lgort ↔ SCM storage_id 확정 매핑. 단 ERP·SCM 매장명 체계가 달라 자동매칭은 34개만 확정(ERP만 83 / SCM만 71 미확정) → 표에 없으면 위 조회로 직접 확인.

자주 쓰는 매장:

| 매장 | ERP (WERKS-LGORT) 쌍 | SCM storage._id |
|---|---|---|
| 무신사 킥스 스타필드 고양점 | `'1000-3058','1700-3053'` | 197 (위탁 개시됨, 2026-08 데이터 有) |
| 무신사 킥스 성수 | `'1000-3057','1700-3048'` | 147 |
| 무신사킥스 홍대 | `'1000-3055','1700-3038'` | 135 |
| 무신사 메가스토어 성수 | `'1000-3532','1700-3049'` | 146 |
| 스탠다드 스타필드 고양 | `'1000-3500'` | 117 |
| 무신사 스탠다드 강남점 | `'1000-3010','1700-3010'` | 28 |

⚠️ `erp_inout_bound`(315)엔 플랜트 컬럼이 없어 `lgort IN ({{ERP_LGORTS}})` 로만 필터. 315는 mvno가 NULL이라 lgort 충돌 매장은 A-번호로 분리 불가(별도 로직 필요). erp_inout_bound는 플랜트 1000·1700 매장을 모두 담는다 — 특정 lgort가 안 보이면 플랜트 문제가 아니라 **그 매장이 아직 매장배치(315) 전 = 전량 이동중**인 경우다(예 2026-08 고양 3053: available_stock=0, transfer_stock=732 → received 0이 정상, 배치되면 채워짐). 확인은 `ocmp.moss.sap_inventory`(plant_code+storage_location: available=입고확정, transfer_stock=이동중).

## 4단계 소스 매핑 (요약)

| 단계 | 매입(ERP) | 위탁(SCM-HUB) |
|---|---|---|
| 출고확정前 | `iif_sap_sto` MENGE (MSTAT=5, BUDAT 빈값) | `stock_movement_item.requested_quantity` |
| 출고확정 | `oif_sap_str` MENGE (BWART=313) | `shipped_quantity` |
| 매장입고확정 | `erp_inout_bound` (**bwart=315 AND shkzg='S'**) | `received_quantity` |
| 이동중 | 출고확정 − 매장입고확정 | 동일 |
| goods_no·사이즈 | `S_MATNR`=goods_no, `OPTION`=사이즈 (직접) | sku→product_option→product (ACTIVE dedup) |

세부 함정은 각 개별 스킬 참조. 핵심: ERP는 S_MATNR/OPTION 직접(바코드·item_id 조인 금지), SCM은 `sku_product_option` ACTIVE 우선 dedup + `CANCELED` 제외.

⚠️ **매입 매장입고확정 = `bwart='315' AND shkzg='S'`** (입고 다리). **절대 `mvno LIKE 'A%'` 쓰지 말 것** — 315는 mvno가 항상 NULL이라 입고확정이 **항상 0**이 되는 버그(2026-08 실측·수정). 315는 입고(S)·전송(H) 두 다리 다 양수라 `shkzg='S'` 필수.

**위탁 확장(선택)**: 매장에서 **나가는** 이동(`fk_source_storage_id`=매장)은 음수(−)로, **직납**(브랜드→매장 직접 입고 = `ocmp.scm_hub.inbound.inbound_type='STORE_INBOUND'`, 물류센터 미경유)은 별도 `구분`으로 포함할 수 있다. 상세는 `scm-hub-inventory-move-flow`의 "매장 출고 흐름 + 직납" 섹션. 아래 표준 쿼리에 반영돼 있음.

## 표준 통합 쿼리

`{{ERP_LGORT}}`(예 `'3048','3057'`), `{{SCM_STORAGE}}`(예 `147`) 교체.

```sql
WITH
-- ===== 매입(ERP) =====
erp_ev AS (
  SELECT '1' stg, NULLIF(S_MATNR,'') goods_no, OPTION opt, CAST(MENGE AS DOUBLE) qty
  FROM pbo.moms.iif_sap_sto
  WHERE CONCAT(WERKS,'-',GR_LGORT) IN ('1000-3058','1700-3053')   -- {{ERP_PAIRS}}
    AND MSTAT='5' AND (CANC IS NULL OR CANC='')
    AND (BUDAT IS NULL OR BUDAT IN ('','00000000'))
  UNION ALL
  SELECT '2', NULLIF(S_MATNR,''), OPTION, CAST(MENGE AS DOUBLE)
  FROM pbo.moms.oif_sap_str
  WHERE CONCAT(WERKS,'-',GR_LGORT) IN ('1000-3058','1700-3053')   -- {{ERP_PAIRS}}
    AND BWART='313' AND (CANC IS NULL OR CANC='')
  UNION ALL
  -- 매장입고확정: 315 입고 다리(shkzg='S'). mvno는 315에 항상 NULL이라 쓰지 말 것(쓰면 항상 0)
  SELECT '3', NULLIF(zz_smatnr,''), zz_option, CAST(menge AS DOUBLE)
  FROM musinsa.stock.erp_inout_bound
  WHERE lgort IN ('3058','3053') AND bwart='315' AND shkzg='S'   -- {{ERP_LGORTS}} (플랜트 없음)
),
erp AS (
  SELECT goods_no, opt AS size,
         SUM(CASE WHEN stg='1' THEN qty ELSE 0 END) AS req,
         SUM(CASE WHEN stg='2' THEN qty ELSE 0 END) AS shp,
         SUM(CASE WHEN stg='3' THEN qty ELSE 0 END) AS rcv
  FROM erp_ev GROUP BY goods_no, opt
),
-- ===== 위탁(SCM-HUB) =====
spo AS (
  SELECT fk_sku_id, fk_product_option_id FROM (
    SELECT fk_sku_id, fk_product_option_id,
      ROW_NUMBER() OVER (PARTITION BY fk_sku_id ORDER BY CASE WHEN mapping_type='ACTIVE' THEN 0 ELSE 1 END, updated_at DESC) rn
    FROM ocmp.scm_hub.sku_product_option) WHERE rn=1
),
-- 위탁 이동: 매장 도착=입고(+1) / 매장 출발=출고(-1) ← 나가는 수량 음수
scm_mv AS (
  SELECT CASE WHEN sm.fk_destination_storage_id=147 THEN '위탁' ELSE '위탁출고' END AS gubun,
         CASE WHEN sm.fk_destination_storage_id=147 THEN 1 ELSE -1 END AS sgn,
         CAST(p.product_no AS STRING) AS goods_no, po.option_name AS size,
         smi.requested_quantity AS req, smi.shipped_quantity AS shp, smi.received_quantity AS rcv
  FROM ocmp.scm_hub.stock_movement sm
  JOIN ocmp.scm_hub.stock_movement_item smi ON smi.fk_stock_movement_id=sm._id AND smi.stock_movement_status<>'CANCELED'
  LEFT JOIN spo ON spo.fk_sku_id=smi.fk_sku_id
  LEFT JOIN ocmp.scm_hub.product_option po ON po._id=spo.fk_product_option_id
  LEFT JOIN ocmp.scm_hub.product p ON p._id=po.fk_product_id
  WHERE sm.fk_destination_storage_id=147 OR sm.fk_source_storage_id=147   -- {{SCM_STORAGE}}
),
scm AS (
  SELECT gubun, goods_no, size, SUM(sgn*req) AS req, SUM(sgn*shp) AS shp, SUM(sgn*rcv) AS rcv
  FROM scm_mv GROUP BY gubun, goods_no, size
),
-- 직납: 브랜드/공급사 → 매장 직접 입고 (물류센터 미경유, STORE_INBOUND), 항상 +
jikn AS (
  SELECT CAST(p.product_no AS STRING) AS goods_no, po.option_name AS size,
         SUM(ii.inbound_requested_quantity) AS req, SUM(ii.inbound_requested_quantity) AS shp,
         SUM(ii.inbound_completed_quantity) AS rcv
  FROM ocmp.scm_hub.inbound ib
  JOIN ocmp.scm_hub.inbound_item ii ON ii.fk_inbound_id=ib._id
  LEFT JOIN ocmp.scm_hub.product_option po ON po._id=ii.fk_product_option_id
  LEFT JOIN ocmp.scm_hub.product p ON p._id=po.fk_product_id
  WHERE ib.inbound_type='STORE_INBOUND' AND ib.fk_destination_storage_id=147   -- {{SCM_STORAGE}}
    AND ii.inbound_item_status<>'CANCELLED'
  GROUP BY p.product_no, po.option_name
),
u AS (
  SELECT '매입' AS gubun, goods_no, size, req, shp, rcv FROM erp
  UNION ALL SELECT gubun, goods_no, size, req, shp, rcv FROM scm
  UNION ALL SELECT '직납', goods_no, size, req, shp, rcv FROM jikn
),
brand AS (
  SELECT goods_id, brand_name, goods_name FROM (
    SELECT goods_id, brand_name, goods_name,
      ROW_NUMBER() OVER (PARTITION BY goods_id ORDER BY (CASE WHEN Op='delete' THEN 1 ELSE 0 END), tr_ts DESC, updated_at DESC) rn
    FROM musinsa.itgg.goods) WHERE rn=1
)
SELECT u.gubun AS `구분`, u.goods_no, b.brand_name AS `브랜드명`, b.goods_name AS `상품명`, u.size AS `사이즈`,
       CAST(u.req AS BIGINT)           AS `출고확정前`,
       CAST(u.shp AS BIGINT)           AS `출고확정`,
       CAST(u.shp - u.rcv AS BIGINT)   AS `이동중`,
       CAST(u.rcv AS BIGINT)           AS `매장입고확정`
FROM u LEFT JOIN brand b ON CAST(b.goods_id AS STRING) = u.goods_no
ORDER BY `구분`, `출고확정前` DESC;
```

## 변형
- **구분별 소계**: 끝에 `GROUP BY 구분` 집계를 덧붙이거나 BI에서 `구분` 소계. (`구분` = 매입 / 위탁 / **위탁출고** / **직납** 4종)
- **매장 하나만(매입 or 위탁)**: 해당 CTE만 남기거나 개별 스킬 사용.
- **위탁 출고·직납 빼기(입고 이동만)**: `scm_mv`의 WHERE를 `fk_destination_storage_id=…`만 남기고, `jikn`/직납 UNION 제거.
- **전량 합계**: goods 컬럼 없이 `구분`별 `SUM`.

## 규칙
- 한글 별칭 **백틱** 필수. 매입/위탁 **코드 체계 다름**(lgort vs storage._id) — 둘 다 지정.
- 매입은 S_MATNR/OPTION 직접, 위탁은 sku_product_option ACTIVE dedup + CANCELED 제외.
- **매입 매장입고확정은 `bwart='315' AND shkzg='S'`** (mvno LIKE 'A%' 금지 — 항상 0 버그).
- **위탁 매장출고(source)는 음수, 직납은 `inbound`(STORE_INBOUND)** — 직납 취소는 `inbound_item_status='CANCELLED'`(L 두 개) 제외.
- 개별: `erp-inventory-move-flow`, `scm-hub-inventory-move-flow`. 배경: 메모리 `inventory-move-confirmed-recipe`.
