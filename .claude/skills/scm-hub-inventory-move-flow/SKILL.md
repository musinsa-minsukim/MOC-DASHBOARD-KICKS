---
name: scm-hub-inventory-move-flow
description: >-
  SCM-HUB 재고이동플로우 — 무신사 위탁(SCM-HUB/WMS) 상품의 허브→매장 재고이동을 단계별
  수량으로 조회한다. goods_no × 사이즈별로 출고확정前 / 출고확정 / 이동중 / 매장입고확정
  4단계를 한 번에 뽑는다. 사용자가 특정 매장으로의 위탁(SCM) 이동지시/출고/입고 현황,
  이동 진행상태(shipped/received), 매장 입고 예정/확정 수량을 물으면 사용한다.
  매장 출고 흐름(매장에서 나가는 수량 음수 표기)·직납(브랜드→매장 직접 입고, STORE_INBOUND)
  포함 조회도 지원한다. ERP(매입) 버전은 erp-inventory-move-flow. Databricks(ocmp.scm_hub + musinsa.itgg) 기반.
---

# SCM-HUB 재고이동플로우 (위탁, 매장 이동)

무신사 **위탁(SCM-HUB / WMS)** 상품이 허브에서 매장으로 이동하는 흐름을 `goods_no × 사이즈` 단위로 4단계 수량으로 조회한다.
매입(ERP/SAP)은 별도 스킬 `erp-inventory-move-flow`. **위탁=SCM-HUB, 매입=ERP** 로 소스가 완전히 다르다.

- 실행: Databricks MCP `mcp__databricks__execute_sql` (없으면 `ToolSearch "select:mcp__databricks__execute_sql"` 로 로드)
- ERP와 달리 SCM-HUB는 **한 테이블(`stock_movement_item`)에 요청/출고/입고 수량이 모두** 있어 훨씬 간단하다.

## 4단계 정의 (모두 `ocmp.scm_hub.stock_movement_item` 컬럼)

| 단계 | 의미 | 컬럼 |
|---|---|---|
| **출고확정 前** | 이동요청 원 수량(baseline, 유지) | `requested_quantity` |
| **출고확정** | 허브에서 실제 출고(반출)된 누적 수량 | `shipped_quantity` |
| **이동중** | 출고됐지만 매장 미입고 = `shipped − received` | (계산) |
| **매장입고확정** | 매장에서 실제 입고된 수량 | `received_quantity` |

`stock_movement_status`(참고용): `CREATED`/`PENDING`=출고前, `PARTIALLY_SHIPPED`=일부출고, `RECEIVED`=입고완료, **`CANCELED`=취소(반드시 제외)**. 4단계 수량은 status와 무관하게 위 3개 컬럼이 직접 나타내며, CANCELED 행만 걸러낸다. (불량분은 `*_defective_quantity` 별도.)

## 매장 코드 = `stock_movement.fk_destination_storage_id` (= `storage._id`)

⚠️ ERP의 lgort(3058 등)와 **다른 체계**다. SCM-HUB는 `ocmp.scm_hub.storage._id` 를 쓴다.

| storage._id | storage_no | 매장 |
|---|---|---|
| 197 | SS030107 | 무신사 킥스 스타필드 고양점 |
| 117 | SS030053 | 스탠다드 스타필드 고양 |
| 184 | SS030094 | 잔스포츠 스타필드 고양점 |
| 147 | SS030070 | 무신사 킥스 성수 |
| 135 | SS030059 | 무신사킥스 홍대 |

다른 매장: `SELECT _id, storage_no, name FROM ocmp.scm_hub.storage WHERE name LIKE '%매장명%'`.
(초도 미개시 매장은 stock_movement에 0건 — 예: 2026-07 기준 고양 킥스 197은 SCM 이동 아직 없음. 오류 아님.)

## goods_no·사이즈 매핑

`stock_movement_item.fk_sku_id` → `sku_product_option`(★ ACTIVE 우선 sku별 1건 dedup) → `product_option`(`option_name`=사이즈, `fk_product_id`) → `product`(`product_no`=goods_no, `product_name`).
- ⚠️ **`sku_product_option` dedup 필수**: sku가 ACTIVE+INACTIVE 두 매핑을 가져 조인 시 ~5% 부풀려짐. `ROW_NUMBER … ORDER BY (mapping_type='ACTIVE' first)` 로 1건만.
- goods_no = `product.product_no`, 사이즈 = `product_option.option_name`(색상^사이즈 형태 가능, 예 "SILVER^250"), 상품명 = `product.product_name`.
- 브랜드명은 goods_no → `musinsa.itgg.goods`(goods_id, brand_name) 조인(선택). `product.brand_code`는 코드라 이름 아님.

## 표준 쿼리

`{{STORAGE}}` 를 매장 `storage._id` 로 교체 (아래는 킥스 성수 147 예시; 고양 킥스는 197).

```sql
WITH
spo AS (   -- sku → product_option (ACTIVE 우선, sku별 1건) ★중복 방지
  SELECT fk_sku_id, fk_product_option_id FROM (
    SELECT fk_sku_id, fk_product_option_id,
      ROW_NUMBER() OVER (PARTITION BY fk_sku_id
        ORDER BY CASE WHEN mapping_type='ACTIVE' THEN 0 ELSE 1 END, updated_at DESC) rn
    FROM ocmp.scm_hub.sku_product_option
  ) WHERE rn=1
),
mv AS (   -- 목적지 매장으로의 이동 라인 (취소 제외)
  SELECT smi.fk_sku_id,
         smi.requested_quantity AS req,
         smi.shipped_quantity   AS shp,
         smi.received_quantity  AS rcv
  FROM ocmp.scm_hub.stock_movement sm
  JOIN ocmp.scm_hub.stock_movement_item smi ON smi.fk_stock_movement_id = sm._id
  WHERE sm.fk_destination_storage_id = 147
    AND smi.stock_movement_status <> 'CANCELED'
),
brand AS (
  SELECT goods_id, brand_name FROM (
    SELECT goods_id, brand_name,
      ROW_NUMBER() OVER (PARTITION BY goods_id
        ORDER BY (CASE WHEN Op='delete' THEN 1 ELSE 0 END), tr_ts DESC, updated_at DESC) rn
    FROM musinsa.itgg.goods
  ) WHERE rn=1
)
SELECT p.product_no AS goods_no,
       b.brand_name    AS `브랜드명`,
       p.product_name  AS `상품명`,
       po.option_name  AS `사이즈`,
       SUM(mv.req)               AS `출고확정前`,
       SUM(mv.shp)               AS `출고확정`,
       SUM(mv.shp) - SUM(mv.rcv) AS `이동중`,
       SUM(mv.rcv)               AS `매장입고확정`
FROM mv
LEFT JOIN spo ON spo.fk_sku_id = mv.fk_sku_id
LEFT JOIN ocmp.scm_hub.product_option po ON po._id = spo.fk_product_option_id
LEFT JOIN ocmp.scm_hub.product p         ON p._id  = po.fk_product_id
LEFT JOIN brand b ON CAST(b.goods_id AS STRING) = CAST(p.product_no AS STRING)
GROUP BY p.product_no, b.brand_name, p.product_name, po.option_name
ORDER BY `출고확정前` DESC;
```

컬럼 순서는 항상 **출고확정前 / 출고확정 / 이동중 / 매장입고확정** (ERP 스킬과 동일).

## 매장 출고 흐름 + 직납 (전체 흐름 조회)

표준 쿼리는 **매장 입고(destination) 이동**만 본다. "매장 출고 흐름"은 매장을 중심으로 **들어오고 나가는 모든 흐름**을 한 표에 보는 것이며, 두 가지가 추가된다:

1. **매장 출고 = 음수(−)**: `fk_source_storage_id`가 해당 매장인 이동(매장→물류센터 반납 SMR008, 매장↔매장 SMR004 출발측 등). 4단계 수량 전체에 부호 −1.
2. **직납 = `inbound` 테이블의 `STORE_INBOUND`** (브랜드/공급사 → 매장 **직접 입고**, 물류센터 미경유). ⚠️ `stock_movement`가 **아니라** 완전히 다른 소스라 표준 쿼리에서는 안 잡힌다. 대부분 오프라인 전용 부자재·케어용품(신발끈·클리너 등).

직납 매핑: `inbound_item.fk_product_option_id`(★sku dedup 불필요, product_option 직결), `inbound_requested_quantity`=입고예정, `inbound_completed_quantity`=매장입고확정. `inbound_item_status <> 'CANCELLED'` 로 취소 제외. 직납은 hub 출고·이동중 개념이 없어 `출고확정前`=`출고확정`=입고예정, `이동중`=예정−확정(미입고 예정분), `매장입고확정`=입고확정 으로 매핑한다.

`구분` 컬럼으로 매장입고(이동)/매장출고(이동)/직납입고 를 구분한다. (`구분`·`GROUP BY`에서 빼면 goods×사이즈별 **순증감(net)** 한 줄로도 볼 수 있다.)

```sql
WITH
spo AS (   -- sku → product_option (ACTIVE 우선 dedup) — 표준 쿼리와 동일
  SELECT fk_sku_id, fk_product_option_id FROM (
    SELECT fk_sku_id, fk_product_option_id,
      ROW_NUMBER() OVER (PARTITION BY fk_sku_id
        ORDER BY CASE WHEN mapping_type='ACTIVE' THEN 0 ELSE 1 END, updated_at DESC) rn
    FROM ocmp.scm_hub.sku_product_option) WHERE rn=1),
mv AS (   -- ① 이동: 매장 도착=입고(+1) / 매장 출발=출고(-1)
  SELECT smi.fk_sku_id,
         CASE WHEN sm.fk_destination_storage_id=147 THEN '매장입고(이동)' ELSE '매장출고(이동)' END AS flow,
         CASE WHEN sm.fk_destination_storage_id=147 THEN 1 ELSE -1 END AS sgn,
         smi.requested_quantity req, smi.shipped_quantity shp, smi.received_quantity rcv
  FROM ocmp.scm_hub.stock_movement sm
  JOIN ocmp.scm_hub.stock_movement_item smi ON smi.fk_stock_movement_id = sm._id
  WHERE (sm.fk_destination_storage_id=147 OR sm.fk_source_storage_id=147)
    AND smi.stock_movement_status <> 'CANCELED'),
mv_po AS (SELECT spo.fk_product_option_id po_id, mv.flow, mv.sgn, mv.req, mv.shp, mv.rcv
          FROM mv LEFT JOIN spo ON spo.fk_sku_id = mv.fk_sku_id),
jikn AS (   -- ② 직납: 브랜드/공급사 → 매장 직접 입고 (STORE_INBOUND), 항상 +
  SELECT ii.fk_product_option_id po_id, '직납입고' flow, 1 sgn,
         ii.inbound_requested_quantity req, ii.inbound_requested_quantity shp,
         ii.inbound_completed_quantity rcv
  FROM ocmp.scm_hub.inbound ib
  JOIN ocmp.scm_hub.inbound_item ii ON ii.fk_inbound_id = ib._id
  WHERE ib.inbound_type='STORE_INBOUND' AND ib.fk_destination_storage_id=147
    AND ii.inbound_item_status <> 'CANCELLED'),
allflow AS (SELECT * FROM mv_po UNION ALL SELECT * FROM jikn),
brand AS (
  SELECT goods_id, brand_name FROM (
    SELECT goods_id, brand_name,
      ROW_NUMBER() OVER (PARTITION BY goods_id
        ORDER BY (CASE WHEN Op='delete' THEN 1 ELSE 0 END), tr_ts DESC, updated_at DESC) rn
    FROM musinsa.itgg.goods) WHERE rn=1)
SELECT p.product_no AS goods_no, b.brand_name AS `브랜드명`, p.product_name AS `상품명`,
       po.option_name AS `사이즈`, a.flow AS `구분`,
       SUM(a.sgn*a.req)         AS `출고확정前`,
       SUM(a.sgn*a.shp)         AS `출고확정`,
       SUM(a.sgn*(a.shp-a.rcv)) AS `이동중`,
       SUM(a.sgn*a.rcv)         AS `매장입고확정`
FROM allflow a
LEFT JOIN ocmp.scm_hub.product_option po ON po._id = a.po_id
LEFT JOIN ocmp.scm_hub.product p ON p._id = po.fk_product_id
LEFT JOIN brand b ON CAST(b.goods_id AS STRING)=CAST(p.product_no AS STRING)
GROUP BY p.product_no, b.brand_name, p.product_name, po.option_name, a.flow
ORDER BY goods_no, `사이즈`, `구분`;
```

## 변형
- **매장 변경**: `fk_destination_storage_id` 값만 교체 (197 고양킥스 / 117 스탠다드고양 등). 여러 매장은 `IN (…)` + `sm.fk_destination_storage_id` 를 SELECT에 추가.
- **이동번호(문서)별**: `sm.stock_movement_id`(또는 `sm._id`)를 `mv`·`GROUP BY`에 추가.
- **전체 합계**: goods 컬럼·GROUP BY 제거하고 `SUM`만.
- **불량 이동 분리**: `requested/shipped/received_defective_quantity` 사용.

## 규칙
- 한글 컬럼 별칭은 **백틱** 필수(`` `출고확정前` ``).
- `sku_product_option` **dedup(ACTIVE 우선) 없으면 수량 부풀려짐** — 반드시 적용.
- `CANCELED` 상태 제외 (이동). 직납은 `inbound_item_status='CANCELLED'` 제외 (L 두 개, 철자 다름 주의).
- 매장코드는 `storage._id`(SCM) — ERP lgort와 혼동 금지.
- **출고 흐름/직납은 위 "매장 출고 흐름 + 직납" 섹션 참조**: 매장 출발(source) 이동은 음수, 직납은 `inbound`(STORE_INBOUND)라 `stock_movement`에 없음.
- 관련 배경: 메모리 `inventory-move-confirmed-recipe`, `stock-business-model-type-code`. 매입 버전은 `erp-inventory-move-flow`.
