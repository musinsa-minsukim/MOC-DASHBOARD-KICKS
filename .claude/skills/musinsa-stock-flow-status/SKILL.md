---
name: musinsa-stock-flow-status
description: >-
  무신사 재고 흐름 상태값 — 파트너사 → MFS 물류센터 → 매장 → 반납 전 구간을 단계별 수량으로 조회한다.
  파트너사 입고 신청 / 물류 하차 완료(입고 확정 전) / 센터 보유(가용·출고제한·할당·출고작업중·패킹완료·비가용) /
  온라인 판매 제외 / 매장 입고 예정(이동지시·출고작업중·패킹완료·출고완료=이동중·직납예정) /
  반납(반납 지시·매장 출고 확정·물류 입고 확정). 매입+위탁 통합, 29CM 화주 제외.
  Use whenever the user asks 재고 상태값·재고 단계, 입고 신청/입고 예정/하차/입고 확정, 가용 재고, 온라인 판매 제외,
  진할당/가할당, 출고 작업중/패킹 완료/출고 완료/이동중, 매장 입고 예정, 반납 지시/매장 출고 확정/물류 입고 확정,
  MWMS·MOMS·SCM-HUB 재고 상태 비교, 화주(MUSINSA/MFS/29CM)별 물류 재고. 원천 = MWMS(pbo.logistics) +
  SCM-HUB(ocmp.scm_hub) + 통합 원장(team.scm.stock). 현재 보유 수량만 필요하면 musinsa-store-stock.
---

# 무신사 재고 흐름 상태값 (파트너사 → 물류 → 매장 → 반납)

재고가 **지금 어느 단계에 얼마나 있는지**를 한 체계로 본다. 2026-11-13 실측 검증.

- 실행: Databricks MCP `mcp__databricks-mcp__execute_sql`. 응답이 없으면 대시보드 레포의 `python tools/dbx.py <파일.sql>`.
- 권한: `pbo.logistics.*`(MWMS 원본)는 **별도 SELECT 권한 필요**. 없으면 `PERMISSION_DENIED` — 데이터팀에 요청.
- 수량 단위는 낱개(EA). 한글 별칭은 백틱.

## 1. 단계 정의 (사용자 확정 2026-11-13)

| 구간 | 단계 | 뜻 | 원천 · 조건 |
|---|---|---|---|
| 입고 | 입고 신청 | 파트너사가 입고예정일을 지정해 신청, 아직 미도착 | MWMS `inbound_plan.inbound_status = 'NEW'` (`inbound_type='NORMAL'`) |
| 입고 | 하차 완료 | 센터 도착, 입고 확정 전 | `inbound_status = 'ARRIVAL'` (`inbound_arrival_date_time` 있음, 확정 0) |
| 입고 | 부분 입고 잔량 | 일부만 확정, 나머지 미확정 | `inbound_status = 'PARTIAL'` 의 예정 − 확정 |
| 센터 보유 | 가용 | 보관 존에 있고 출고 가능 | MWMS `inventory_status='AVAILABLE'` + `mst_zone.type='STORE'` + 출고제한 아님 |
| 센터 보유 | 온라인 판매 제외 | 온라인에 풀지 않고 묶어둔 수량 | **MWMS에 없음** → `team.scm.stock.online_except_quantity` (가용 안에 포함된 값) |
| 센터 보유 | 출고제한 | 출고제한일 도래로 할당 불가 | `is_assign_blocked = 1` |
| 센터 보유 | 할당(출고작업전) | 출고 지시에 재고가 배정됨, 피킹 전 | `inventory_status = 'ASSIGNED'` |
| 센터 보유 | 출고작업중 | 피킹됨, 패킹 전 | `inventory_status IN ('PICKED','MOVED_TO_TOTE','MOVING')` |
| 센터 보유 | 패킹완료 | 패킹됨, 출고 확정 전 | `inventory_status = 'PACKED'` |
| 센터 보유 | 비가용 | 불량·분실·입고대기 등 존 | `AVAILABLE` 이지만 `mst_zone.type <> 'STORE'` |
| 매장 입고 예정 | 이동지시(출고작업전) | 보충·이동 지시, 센터 작업 전 | SCM-HUB 요청 − 출고(미결 문서)에서 아래 두 단계를 뺀 나머지 |
| 매장 입고 예정 | 출고작업중 | 센터에서 피킹 진행·완료, 패킹 전 | MWMS `outbound_item.picked_quantity − packed_quantity` (미확정 출고) |
| 매장 입고 예정 | 패킹완료 | 물류 패킹 완료, 출고 확정 전 | MWMS `outbound_item.packed_quantity` (미확정 출고) |
| 매장 입고 예정 | 출고완료 = 이동중 | 센터 출고 확정, 매장 입고확정 전 | SCM-HUB `shipped_quantity − received_quantity` |
| 매장 입고 예정 | 직납예정 | 브랜드→매장 직접 입고(센터 미경유) 미확정분 | SCM-HUB `inbound`(`STORE_INBOUND`) 요청 − 확정 |
| 반납 | 반납 지시 | 매장→센터 반납 요청 수량 | SCM-HUB `requested_quantity` (출발지=매장, 도착지=센터) |
| 반납 | 매장 출고 확정 | 매장이 실제로 내보낸 수량 | `shipped_quantity` |
| 반납 | 물류 입고 확정 | 센터가 입고 확정한 수량 | `received_quantity`. 브랜드사 반출 건은 매장 출고 확정과 동시에 확정 처리 |

- 매장 입고 예정 5단계는 **서로 겹치지 않는다**(합 = 입고 예정 합계).
- 반납 3단계는 **누적**이다: 반납 지시 ≥ 매장 출고 확정 ≥ 물류 입고 확정 (최근 60일 생성 문서 기준, 취소 제외).
- "반납 확정"은 **매장 출고 시점**이 기준이고, 물류 입고 확정은 따로 본다.

## 2. 원천 3종과 역할

| 원천 | 스키마 | 아는 것 | 모르는 것 |
|---|---|---|---|
| MWMS (창고관리) | `pbo.logistics` | 입고 예정·도착·확정, 로케이션·존, 재고 상태, 출고 지시의 할당·피킹·패킹·출고, 화주 | 매장 입고확정, 온라인 판매 제외 |
| SCM-HUB | `ocmp.scm_hub` | **매입·위탁 모두**의 이동 문서(요청·출고·입고확정), 직납, 반납, 스토리지 | 센터 내 작업 단계. 매입 **재고 수량**은 `inventory` 에 없음(12개뿐) |
| 통합 원장 (SLS·IVL) | `team.scm.stock*` | SKU×거점 보유, 진할당·가할당·잔여, 온라인 판매 제외 | 플랫폼·화주 구분, 작업 단계 |

- **이동·입고확정·반납은 매입도 SCM-HUB에서 본다.** ERP(`oif_sap_str`/`erp_inout_bound`)와 합치면 이중 집계된다.
- **보유 수량은 통합 원장 또는 MWMS에서 본다.** SCM-HUB `inventory` 로 매입 재고를 보면 0에 가깝다.
- 세 시스템의 센터 전체 수량은 거의 같다(약 2,451만~2,460만). 가용 약 2,415만은 MWMS·MOMS가 같은 뜻으로 쓴다.

## 3. 반드시 아는 사실

- **화주(`mst_shipper.code`)가 물리 소유 구분**이다. `MUSINSA`=1P 매입, `MFS-번호`=위탁(약 1,019개사, `logistics_service_type='MFS'`),
  `INOOINT`=MUSINSATRADING, `29CM`·`29CM_NIKE`=29CM, 그 외 `NIKE`·`MUSINSA_USED`·`GLOWNY`·`MUSINSAGLOBAL` 등.
  무신사 물량만 보려면 `s.code NOT LIKE '29CM%'`. (보유: MUSINSA 1,893만 / MFS 218만 / INOOINT 137만 / 29CM 101만)
- **통합 원장은 플랫폼으로 나뉘지 않은 물리 재고**다. SKU 매핑(`platform='MUSINSA'`)은 상품번호만 붙이고 수량을 나누지 않는다.
- **원장 항등식**: 총 보유 = 잔여 + 진할당 + 가할당. 진할당 = MWMS가 출고 작업에 배정, 가할당 = MOMS가 먼저 잡아둔 예약.
  **온라인 판매 제외는 가할당 안에 들어 있다**(행 단위 98%가 `가할당 = 온라인 판매 제외 + B2B 출고예정`).
  그래서 잔여(할당 제외)에는 온라인 판매 제외분이 빠져 있다.
- **MWMS 매장 코드 `to_shop_code` = SCM-HUB `site.site_code`** (`ST3110` 형태). ERP 저장위치가 아니다(4절 표).
- `outbound.order_type`: 매장행은 `BETWEEN_CENTER`(대부분)·`IN_HOUSE`. 매장 유형은 `mst_retail_store.store_type`
  (`B2B_SELECTSHOP`=편집샵·킥스·뷰티, `B2B_STORE`=무신사 스탠다드).
- SCM-HUB 스토리지: 센터는 `ONLINE`/`KEEPING`/`OFFLINE`+`NORMAL`(센터별 "…-무신사-온라인·오프라인·입고보관", 29CM 별도),
  매장은 `OFFLINE`+`MUSINSA_STORE`/`MUSINSA_KICKS`/`MUSINSA_BEAUTY`/`MUTANDARD`/`TRADING`/`CM29_*`.
- 반출(브랜드사로 나가는 건)은 `stock_movement.outbound_shipping_type` 이 기록된 건이고, 데이터상 출고 = 입고확정으로 함께 찍힌다.
- 택배 추적(`invoice_tracking_info`)에는 수거완료(`COLLECTION_COMPLETED`)·허브 상하차·배송출발·배송완료가 있다.
  **용차(매장 출고 대부분)는 배차 상태값이 없다.**
- **매핑 대표 플래그(2026-11-12 17시 변경, 데이터 담당 공지)**: 열 이름이 `is_rep_uid` → `is_rep_uid_option_code`, `is_rep_uid_musinsa` → `is_rep_uid_option_code_musinsa`, `is_rep_uid_cm29` → `is_rep_uid_option_code_cm29` 로 바뀌었다. 대표 기준이 "SKU당 대표 uid"에서 "SKU당 대표 (uid, option_code) 한 쌍"으로 바뀐 것. 옛 이름을 쓰면 `UNRESOLVED_COLUMN` 으로 실패한다.
  - **무신사 상품번호(goods_no)를 붙일 땐 `platform='MUSINSA' AND is_rep_uid_option_code_musinsa = TRUE`** → SKU당 정확히 1행(2026-11-13 검증: 2,205,689 SKU, 중복 0).
  - `is_rep_uid_option_code` 만 걸어도 SKU당 1행이지만(2,321,236 SKU) 그중 122,313개는 대표가 29CM 상품번호다 → 무신사 상품번호가 필요하면 쓰지 말 것.
  - 일별 이력 `team.scm.mst_sku_mapping_uid_option_code_snapshot` 은 2026-10-16~11-12 이력이 지워지고 11-13부터 새 열로 다시 쌓인다(11-13 저녁 조회 시 테이블 없음).

## 4. 매장 코드 크로스워크 (대시보드 오프라인 매장)

`team.commercepm.offline_shopno_storageid`(shop_no ↔ storage_id) → `ocmp.scm_hub.storage` → `ocmp.scm_hub.site.site_code`.

| shop_no | 매장 | SCM storage_id | site_code (MWMS) |
|---|---|---|---|
| 7 | 무신사 스토어 대구 | 51 | ST3025 |
| 8 | 무신사 스토어 홍대 | 52 | ST3026 |
| 25 | 무신사 스토어 성수 | 53 | ST3027 |
| 55 | 무신사 스토어 강남 | 105 | ST3042 |
| 67 | 무신사 걸즈 타임스퀘어 영등포점 | 130 | ST3054 |
| 68 | 무신사 메가스토어 용산 | 131 | ST3055 |
| 79 | 무신사 킥스 홍대 | 135 | ST3059 |
| 82 | 무신사 스토어 롯데백화점 잠실점 | 137 | ST3060 |
| 83 | 무신사 스토어 명동 | 138 | ST3061 |
| 86 | 무신사 아울렛&유즈드 롯데몰 은평점 | 145 | ST3068 |
| 88 | 무신사 메가스토어 성수 | 146 | ST3069 |
| 89 | 무신사 킥스 성수 | 147 | ST3070 |
| 90 | 무신사 런 서울숲 | 153 | ST3076 |
| 91 | 무신사 백앤캡클럽 서울숲 | 148 | ST3071 |
| 98 | 무신사 스토어 AK플라자 수원점 | 154 | ST3077 |
| 122 | 무신사 스토어 트리플스트리트 송도점 | 157 | ST3080 |
| 136 | 무신사 킥스 스타필드 고양점 | 197 | ST3110 |
| 137 | 무신사 뷰티 홍대 | 198 | ST3111 |
| 149 | 무신사 백앤캡클럽 청량리 | 216 | ST3119 |
| 151 | 무신사 뷰티 성수 | 266 | ST3129 |

## 5. 표준 쿼리

### A. 입고 신청 · 하차 완료 (파트너사 → 센터)

```sql
SELECT
  CASE WHEN p.inbound_status = 'NEW'     THEN '1.입고 신청(미도착)'
       WHEN p.inbound_status = 'ARRIVAL' THEN '2.하차 완료(입고 확정 전)'
       ELSE '3.부분 입고(잔량)' END                                   AS `단계`,
  CASE WHEN s.code = 'MUSINSA' THEN '매입' WHEN s.logistics_service_type = 'MFS' THEN '위탁' ELSE '기타' END AS `구분`,
  COUNT(DISTINCT p.id)                                               AS `입고예정건`,
  SUM(i.planned_quantity)                                            AS `예정수량`,
  SUM(COALESCE(i.fixed_quantity, 0))                                 AS `확정수량`,
  SUM(GREATEST(i.planned_quantity - COALESCE(i.fixed_quantity, 0), 0)) AS `미확정수량`
FROM pbo.logistics.inbound_plan p
JOIN pbo.logistics.inbound_plan_item i ON i.inbound_plan_id = p.id
JOIN pbo.logistics.mst_shipper s       ON s.id = p.mst_shipper_id
JOIN pbo.logistics.mst_warehouse w     ON w.id = p.mst_warehouse_id
WHERE p.inbound_type = 'NORMAL'
  AND p.inbound_status IN ('NEW', 'ARRIVAL', 'PARTIAL')
  AND p.inbound_planned_date >= date_format(date_sub(current_date(), 30), 'yyyy-MM-dd')
  AND s.code NOT LIKE '29CM%'
  {{FILTER}}
GROUP BY 1, 2
ORDER BY 1, 2;
```

- `{{FILTER}}` 예: 센터 `AND w.code = 'NWH03'` / 상품 `AND i.sku_id = 'S2606240014602'` / 브랜드 `AND i.brand = '나이키'` / 화주 `AND s.code = 'MFS-508'`.
- 상세로 보려면 SELECT 를 `p.inbound_plan_number, p.inbound_planned_date, p.inbound_arrival_date_time, i.item_barcode, i.sku_id, i.item_name` 로 바꾸고 GROUP BY 제거.
- `inbound_planned_date` 는 문자열 `yyyy-MM-dd`. `inbound_type`: `NORMAL`(일반) / `RETURN`(고객 반품) / `BETWEEN`(센터 간) / `TRANSFER`(대부분 글로벌센터) / `RETURN_STORE`.
- 입고 상태값: `NEW` → `ARRIVAL` → `PARTIAL` → `COMPLETE` (+ `CANCEL`, `DOCUMENT_CLOSED`).
- 택배 입고의 하차 시각은 `inbound_handling_unit_tracking.delivered_at`(`delivery_status='DELIVERED'`).
- 검증(2026-11-13): 입고 신청 매입 374,025 / 위탁 325,166, 하차 완료 매입 95,905 / 위탁 178,526.

### B. 센터 보유 상태 (MWMS, 화주·존 기준)

```sql
SELECT
  w.code AS `센터코드`, MAX(w.title) AS `센터`,
  CASE WHEN s.code = 'MUSINSA' THEN '매입' WHEN s.logistics_service_type = 'MFS' THEN '위탁' ELSE s.code END AS `화주구분`,
  SUM(i.total_qty)                                                                        AS `보유`,
  SUM(CASE WHEN i.inventory_status = 'AVAILABLE' AND z.type = 'STORE' AND COALESCE(i.is_assign_blocked,0) = 0 THEN i.total_qty ELSE 0 END) AS `가용`,
  SUM(CASE WHEN i.inventory_status = 'AVAILABLE' AND z.type = 'STORE' AND COALESCE(i.is_assign_blocked,0) = 1 THEN i.total_qty ELSE 0 END) AS `출고제한`,
  SUM(CASE WHEN i.inventory_status = 'ASSIGNED' THEN i.total_qty ELSE 0 END)              AS `할당(출고작업전)`,
  SUM(CASE WHEN i.inventory_status IN ('PICKED','MOVED_TO_TOTE','MOVING') THEN i.total_qty ELSE 0 END) AS `출고작업중`,
  SUM(CASE WHEN i.inventory_status = 'PACKED' THEN i.total_qty ELSE 0 END)                AS `패킹완료`,
  SUM(CASE WHEN i.inventory_status = 'AVAILABLE' AND z.type <> 'STORE' THEN i.total_qty ELSE 0 END) AS `비가용존(불량·분실·입고대기 등)`
FROM pbo.logistics.inventory i
JOIN pbo.logistics.mst_shipper s   ON s.id = i.mst_shipper_id
JOIN pbo.logistics.mst_warehouse w ON w.id = i.mst_warehouse_id
JOIN pbo.logistics.mst_location l  ON l.id = i.mst_location_id
JOIN pbo.logistics.mst_zone z      ON z.id = l.mst_zone_id
WHERE COALESCE(i.is_deleted, 0) = 0
  AND s.code NOT LIKE '29CM%'
  {{FILTER}}
GROUP BY 1, 3
ORDER BY 4 DESC;
```

- `{{FILTER}}` 예: `AND i.sku_id IN ('S…')` / `AND w.code = 'NWH01'` / `AND s.code = 'MUSINSA'`.
- 여기의 할당·출고작업중·패킹완료는 **온라인 주문(B2C)과 매장행(B2B)을 합친** 센터 전체 작업량이다. 매장행만 보려면 D.
- 존 유형(`mst_zone.type`): `STORE`(보관) / `DEFECT`(불량) / `LOST`(분실) / `RECEIVE`(입고대기) / `MULT_PACK`·`SOLE_PACK`(패킹) /
  `SHIP`(출하) / `TMPPWY`·`TEMP` / `TRD_SORTER` / `RETURN` / `PICK_CANCEL` / `PACK_HOLD` / `DROP_ZONE` / `TOTE`.
- 상품번호가 필요하면 `i.sku_id` 를 `(SELECT DISTINCT sku_id, uid FROM team.scm.mst_sku_mapping_uid_option_code WHERE platform='MUSINSA' AND is_rep_uid_option_code_musinsa)` 에 조인.
- 검증(2026-11-13): 신여주1 매입 보유 10,159,214 / 가용 9,923,145 / 할당 52,042 / 출고작업중 56,025 / 패킹완료 5,384.

### C. 온라인 판매 제외 · 할당 (통합 원장)

```sql
WITH mp AS (SELECT DISTINCT sku_id, uid FROM team.scm.mst_sku_mapping_uid_option_code
            WHERE platform = 'MUSINSA' AND is_rep_uid_option_code_musinsa = TRUE)
SELECT s.root_node_id AS `센터코드`, MAX(s.node_name) AS `센터`, m.uid AS goods_no,
       SUM(s.total_quantity)              AS `총보유`,
       SUM(s.remaining_quantity)          AS `잔여(할당제외)`,
       SUM(s.allocation_quantity)         AS `진할당`,
       SUM(s.virtual_allocation_quantity) AS `가할당`,
       SUM(s.online_except_quantity)      AS `온라인판매제외`
FROM team.scm.stock s
LEFT JOIN mp m ON m.sku_id = s.sku_id
WHERE s.namespace = 'FC'
  {{FILTER}}
GROUP BY s.root_node_id, m.uid
ORDER BY `온라인판매제외` DESC;
```

- `{{FILTER}}` 예: `AND m.uid IN (6748800)` / `AND s.root_node_id = 'NWH01'`.
- 온라인 판매 제외는 SKU×센터 단위다(바코드·로케이션 단위 없음). 온라인 판매가능(`online_available_quantity`)은 일별 스냅샷
  `team.scm.stock_snapshot_daily`(파티션 `snapshot_date` WHERE 필수)에만 있다.
- 이 값에는 29CM 화주 몫이 섞일 수 있다(원장에 화주 구분 없음). 정확히 나누려면 B의 화주 기준 수량을 쓴다.
- 검증(2026-11-12): 센터 전체 총보유 24,514,436 = 잔여 20,472,962 + 진할당 154,958 + 가할당 3,886,516, 온라인 판매 제외 3,861,294
  (여주1 260만, 안성 68만, 약 98.7%가 매입).

### D. 매장 흐름 — 입고 예정 5단계 + 반납 3단계 ★주력

```sql
WITH sid AS (   -- 매장(shop_no) ↔ SCM-HUB 스토리지 ↔ 사이트 코드(= MWMS to_shop_code)
    SELECT CAST(x.storage_id AS BIGINT) AS sid, x.shop_name AS store_name, si.site_code
    FROM team.commercepm.offline_shopno_storageid x
    JOIN ocmp.scm_hub.storage st ON CAST(st._id AS BIGINT) = CAST(x.storage_id AS BIGINT)
    LEFT JOIN ocmp.scm_hub.site si ON si._id = st.fk_site_id
    WHERE CAST(x.shop_no AS INT) IN ({{SHOP_NOS}})),
ctr AS (   -- 센터 스토리지(온라인·입고보관·센터 오프라인)
    SELECT CAST(_id AS BIGINT) AS sid FROM ocmp.scm_hub.storage
    WHERE storage_type IN ('ONLINE','KEEPING') OR (storage_type = 'OFFLINE' AND storage_detail_type = 'NORMAL')),
spo AS (SELECT fk_sku_id, fk_product_option_id FROM (
    SELECT fk_sku_id, fk_product_option_id, ROW_NUMBER() OVER (PARTITION BY fk_sku_id
      ORDER BY CASE WHEN mapping_type='ACTIVE' THEN 0 ELSE 1 END, updated_at DESC) rn
    FROM ocmp.scm_hub.sku_product_option) WHERE rn = 1),
mv AS (    -- 미취소 이동 품목(매입·위탁 모두)
    SELECT sm.fk_source_storage_id AS src, sm.fk_destination_storage_id AS dst, sm.created_at,
           sm.outbound_shipping_type, smi.fk_sku_id, smi.stock_movement_status AS st,
           CAST(smi.requested_quantity AS DOUBLE) AS req, CAST(smi.shipped_quantity AS DOUBLE) AS shp,
           CAST(smi.received_quantity AS DOUBLE) AS rcv
    FROM ocmp.scm_hub.stock_movement sm
    JOIN ocmp.scm_hub.stock_movement_item smi
      ON smi.fk_stock_movement_id = sm._id AND smi.stock_movement_status NOT IN ('CANCELED','FAILED')),
inb AS (   -- 매장 입고(도착지 = 매장)
    SELECT s.store_name, mv.fk_sku_id,
           SUM(CASE WHEN mv.st IN ('CREATED','PENDING','PARTIALLY_SHIPPED') THEN GREATEST(mv.req - mv.shp, 0) ELSE 0 END) AS pend,
           SUM(CASE WHEN mv.st <> 'RECEIVED' THEN GREATEST(mv.shp - mv.rcv, 0) ELSE 0 END) AS transit
    FROM mv JOIN sid s ON mv.dst = s.sid GROUP BY 1, 2),
ret AS (   -- 매장 → 센터 반납(최근 60일 생성 문서 누적)
    SELECT s.store_name, mv.fk_sku_id,
           SUM(mv.req) AS ret_req, SUM(mv.shp) AS ret_store_out,
           SUM(CASE WHEN mv.outbound_shipping_type IS NOT NULL THEN GREATEST(mv.shp, mv.rcv) ELSE mv.rcv END) AS ret_center_in
    FROM mv JOIN sid s ON mv.src = s.sid JOIN ctr c ON c.sid = mv.dst
    WHERE mv.created_at >= date_sub(current_date(), 60) GROUP BY 1, 2),
mw AS (    -- MWMS 미확정 매장행 출고의 작업 단계
    SELECT s.store_name, CAST(k._id AS BIGINT) AS fk_sku_id,
           SUM(COALESCE(i.packed_quantity, 0)) AS packed,
           SUM(GREATEST(COALESCE(i.picked_quantity, 0) - COALESCE(i.packed_quantity, 0), 0)) AS work
    FROM pbo.logistics.outbound o
    JOIN pbo.logistics.outbound_item i ON i.outbound_id = o.id
    JOIN sid s ON s.site_code = o.to_shop_code
    JOIN ocmp.scm_hub.sku k ON k.sku_id = i.sku_id
    WHERE o.outbound_status IN ('NEW','CREATED_WAVE') AND o.created_date >= date_sub(current_date(), 90)
    GROUP BY 1, 2),
dir AS (   -- 브랜드 직납(센터 미경유)
    SELECT s.store_name, CAST(ii.fk_sku_id AS BIGINT) AS fk_sku_id,
           SUM(GREATEST(COALESCE(ii.inbound_requested_quantity,0) - COALESCE(ii.inbound_completed_quantity,0), 0)) AS inq
    FROM ocmp.scm_hub.inbound ib
    JOIN ocmp.scm_hub.inbound_item ii ON ii.fk_inbound_id = ib._id AND ii.inbound_item_status <> 'CANCELLED'
    JOIN sid s ON ib.fk_destination_storage_id = s.sid
    WHERE ib.inbound_type = 'STORE_INBOUND' GROUP BY 1, 2),
keys AS (SELECT store_name, fk_sku_id FROM inb UNION SELECT store_name, fk_sku_id FROM ret UNION SELECT store_name, fk_sku_id FROM dir),
j AS (
    SELECT k.store_name, k.fk_sku_id,
           COALESCE(i.pend, 0) AS pend, COALESCE(i.transit, 0) AS transit,
           LEAST(COALESCE(m.packed, 0), COALESCE(i.pend, 0)) AS packed, COALESCE(m.work, 0) AS work_raw,
           COALESCE(d.inq, 0) AS direct,
           COALESCE(r.ret_req, 0) AS ret_req, COALESCE(r.ret_store_out, 0) AS ret_store_out, COALESCE(r.ret_center_in, 0) AS ret_center_in
    FROM keys k
    LEFT JOIN inb i ON i.store_name = k.store_name AND i.fk_sku_id = k.fk_sku_id
    LEFT JOIN ret r ON r.store_name = k.store_name AND r.fk_sku_id = k.fk_sku_id
    LEFT JOIN mw m  ON m.store_name = k.store_name AND m.fk_sku_id = k.fk_sku_id
    LEFT JOIN dir d ON d.store_name = k.store_name AND d.fk_sku_id = k.fk_sku_id),
f AS (SELECT j.*, LEAST(j.work_raw, j.pend - j.packed) AS work FROM j)
SELECT f.store_name AS `매장`, CAST(p.product_no AS BIGINT) AS goods_no, po.option_name AS `옵션`,
       CAST(SUM(f.pend - f.packed - f.work) AS BIGINT) AS `이동지시(출고작업전)`,
       CAST(SUM(f.work) AS BIGINT)    AS `출고작업중`,
       CAST(SUM(f.packed) AS BIGINT)  AS `패킹완료`,
       CAST(SUM(f.transit) AS BIGINT) AS `출고완료(이동중)`,
       CAST(SUM(f.direct) AS BIGINT)  AS `직납예정`,
       CAST(SUM(f.pend + f.transit + f.direct) AS BIGINT) AS `입고예정_합계`,
       CAST(SUM(f.ret_req) AS BIGINT)       AS `반납지시`,
       CAST(SUM(f.ret_store_out) AS BIGINT) AS `매장출고확정`,
       CAST(SUM(f.ret_center_in) AS BIGINT) AS `물류입고확정`
FROM f
LEFT JOIN spo ON spo.fk_sku_id = f.fk_sku_id
LEFT JOIN ocmp.scm_hub.product_option po ON po._id = spo.fk_product_option_id
LEFT JOIN ocmp.scm_hub.product p ON p._id = po.fk_product_id
WHERE f.pend + f.transit + f.direct > 0 OR f.ret_req > 0
GROUP BY f.store_name, CAST(p.product_no AS BIGINT), po.option_name
ORDER BY `입고예정_합계` DESC, `반납지시` DESC;
```

- `{{SHOP_NOS}}` = 4절의 shop_no 목록(예 `79, 136`). 매장 2곳 기준 약 15초, 20곳 전체 약 22초.
- 특정 상품만: 마지막 WHERE 에 `AND CAST(p.product_no AS BIGINT) IN (…)` 추가.
- 매장 합계만: SELECT·GROUP BY 에서 `goods_no`, `옵션` 을 뺀다.
- 입고 예정의 작업 단계는 매장×SKU 단위로 MWMS 수량을 SCM-HUB 미출고 수량 안에서 배분한다(패킹완료 → 출고작업중 → 나머지가 이동지시).
- 매장 간 이동(도착지가 다른 매장)은 반납에 넣지 않는다. 받는 매장 쪽에서는 입고 예정(출고완료)으로 잡힌다.
- 직납예정에는 도착했지만 확정 전인 건(`inbound_item_status='ARRIVED'`)이 포함된다. 뷰티 매장은 이 값이 크다(뷰티 성수 약 44만).
- 검증(2026-11-13): 킥스 홍대 이동지시 932 / 출고작업중 187 / 패킹완료 192 / 출고완료 458 / 직납예정 146,
  반납지시 9,513 / 매장출고확정 7,584 / 물류입고확정 5,301. 대시보드 20개 매장 합: 이동지시 23,339 / 출고작업중 4,695 /
  패킹완료 1,384 / 출고완료 22,875 / 직납예정 682,091, 반납지시 421,421 / 매장출고확정 319,110 / 물류입고확정 278,766.

## 6. 상태값 사전

| 대상 | 컬럼 | 값 |
|---|---|---|
| MWMS 재고 | `inventory.inventory_status` | `AVAILABLE` / `ASSIGNED` / `PICKED` / `PACKED` / `MOVED_TO_TOTE` / `MOVING` (+ `is_assign_blocked`) |
| MWMS 입고 | `inbound_plan.inbound_status` | `NEW` / `ARRIVAL` / `PARTIAL` / `COMPLETE` / `CANCEL` / `DOCUMENT_CLOSED` |
| MWMS 출고 | `outbound.outbound_status` | `NEW` / `CREATED_WAVE` / `COMPLETED` / `CANCEL` |
| MWMS 출고 작업 | `assign_status` · `picking_status` · `packing_status` | `NOTHING` / `READY` / `PARTIAL` / `COMPLETE` (소팅은 `READY`/`WORKING`/`COMPLETE`) |
| MWMS 택배 추적 | `invoice_tracking_info.status` | `COLLECTION_COMPLETED` / `HUB_LOADING` / `HUB_UNLOADING` / `DELIVERY_STARTED` / `DELIVERED` / `NOT_DELIVERED` |
| SCM-HUB 이동 | `stock_movement_item.stock_movement_status` | `CREATED` / `PENDING` / `PARTIALLY_SHIPPED` / `RECEIVED` / `CANCELED` / `FAILED` |
| SCM-HUB 직납 | `inbound_item.inbound_item_status` | `EXPECTED` / `ARRIVED` / `PARTIALLY_INBOUNDED` / `ENTIRELY_INBOUNDED` / `CANCELLED` |
| SCM-HUB 반납 유형 | `stock_movement.return_type` | `PLAN` / `SEASON` / `ETC` / `DEFECT` / `DISPOSAL` (반납 건에만 기록) |
| MOMS 출고지시 | `pbo.moms.opr_do_status.ORD_STATUS` | 미할당 / 할당 / 출고재계획 / 출고취소 |

## 7. 규칙 · 함정

- 무신사 물량만 볼 때 29CM 화주 제외(`s.code NOT LIKE '29CM%'`). 통합 원장만으로는 화주를 못 나눈다.
- SCM-HUB 이동은 `CANCELED`·`FAILED` 제외, SKU→옵션은 `sku_product_option` 의 `ACTIVE` 우선 1건.
- 직납 취소 상태는 `CANCELLED`(L 두 개), 이동 취소는 `CANCELED`(L 한 개).
- 오래 묵은 미결 문서(수개월 전 `PENDING`)도 그대로 집계된다. 정리 기준은 정해지지 않았다(사용자: 우선 그대로 포함).
- `pbo.moms.opr_inventory` 는 재고가 아니라 일별 입고 실적이다. MOMS 현재고는 `pbo.moms.opr_inv`(전체/가용만, 할당 0).
- `team.scm.movement_center_to_store_status` 의 `5.Sending_Ready` 를 이동중으로 쓰지 말 것 — 매장 입고와 연결 안 된 건이
  2025년 1월분부터 누적돼 크게 부푼다. 이동중은 D의 `출고완료(이동중)` 으로 본다.
- 파티션 키가 있는 테이블(`stock_snapshot_daily`=`snapshot_date`, `ciap_*`=`snapshot_date`, `mst_sku_mapping_platform_stock_ratio`=`snapshot_date`)은 WHERE 필수.

## 8. 아직 정해지지 않은 것

- 온라인 판매 제외 물량이 오프라인(매장 출고)용인지 다른 용도인지 — SCM 담당 확인 필요. 이에 따라 "매장으로 보낼 수 있는 수량" 정의가 갈린다.
- 사용자가 말한 '중간 슬롯'의 대응 값 — 후보: 무신사·29CM 양쪽에 매핑된 공용 SKU, 화주 `INOOINT`(MUSINSATRADING), SCM-HUB `KEEPING`(입고보관) 스토리지.
- 매장 재고에 잡힌 진할당·가할당(각 약 15만)의 의미.
- 용차 배차 상태를 볼 원천(운송 시스템).

## 9. 대시보드 재고 탭 열과의 대응 (2026-11-13 사용자 정의)

재고 탭 상품옵션 표는 아래 열을 쓴다(원천 `db.fetch_store_flow`, MWMS 작업 단계는 합쳐서 본다).

| 화면 열 | 이 스킬의 단계 | 계산 |
|---|---|---|
| 점재고(가용) | 매장 총 보유 − 원장 할당 | 원장 `remaining_quantity`. 할당 = 진할당+가할당(실측상 거의 전부 반납 지시분). 할당이 보유보다 많으면 0. **대시보드의 모든 점재고 지표가 이 기준**(재고·판매·요약·드릴) |
| 입고 예정 | 입고 지시 + 이동중 | – |
| 입고 지시 | 이동지시 + 출고작업중 + 패킹완료 (+ 직납 도착 전) | 요청 − 센터 출고 |
| 이동중 | 출고완료 (+ 직납 도착·확정 전) | 센터 출고 − 매장 입고확정 |
| 반출 예정 (대시보드 재고 탭) | 반납 지시 중 매장 출고 확정 전 | 요청 − 매장 출고 · 아직 매장에 있는, 앞으로 나갈 물량. **대시보드는 이 한 열만 쓴다**(2026-11-14 사용자 결정) — '요청 − 물류 입고확정'은 이미 나간 물량까지 세어 점재고보다 커 보임(실측 차이 26%) |
| 반납 지시 | 반납 지시 중 매장 출고 확정 전 | 요청 − 매장 출고 (매장 간 이동 포함) |
| 입고 지시일 | 남아 있는 입고 지시·이동중 문서 | 그 라인들의 `created_at` 중 **가장 최근 날짜**(KST). 직납은 `inbound_item.created_at` |
| 반출 지시일 | 남아 있는 반납·매장간 이동 지시 | 그 라인들의 `created_at` 중 **가장 최근 날짜**(KST) |

- 물류(센터) 재고는 대시보드에서 세 열이다: **총 물류 보유**(`total_quantity`) / **무신사 슬롯 재고(가용)**(`remaining_quantity`, 매장·판매에 풀 수 있는 물량) / **온라인 판매 제외**(`online_except_quantity`, SKU 단위라 바코드 비례 배분). 실측(2026-11-14, 대시보드 범위): 총 보유 15,207,995 = 가용 11,724,498 + 온라인 제외 3,332,210 + 나머지 151,287(진할당·B2B 출고예정).
- **지시일(2026-11-14 추가)**: `ocmp.scm_hub` 의 `created_at` 은 **KST 벽시계**로 적재된다 — 업무시간 9~18시에 몰리고 `MAX(created_at)` 이 `current_timestamp()`(UTC)보다 9시간 앞선다. `from_utc_timestamp` 로 다시 변환하면 9시간 틀어진다. 반면 `team.scm.stock.updated_at` 은 UTC 라 변환이 필요하다 — 테이블마다 다르니 확인 후 쓴다.
  대시보드는 90일 넘게 남아 있는 지시를 호박색으로 표시한다(오래 안 끝난 문서 찾기용). 실측(2026-11-14): 가장 오래된 미결 입고 지시 2025-09-29, 반출 지시 2025-11-20.
- 출고 예정이 점재고보다 클 수 있다: 매장이 이미 내보낸 수량(점재고에서는 빠짐)이 물류 입고확정 전까지 출고 예정에 남기 때문.
- 미결 문서만 센다(`CREATED`/`PENDING`/`PARTIALLY_SHIPPED`). D 쿼리의 반납 3단계(최근 60일 누적)와는 집계 범위가 다르다.

## 관련 스킬

- 현재 보유 수량(매장·센터, 매입+위탁): `musinsa-store-stock`
- 매장 코드(shop_no ↔ storage_id ↔ ERP): `musinsa-store-master`
- 상품 속성·가격: `musinsa-goods-master`
- 이동 문서 4단계(출고확정前/출고확정/이동중/매장입고확정)를 goods_no×사이즈로 보려면: `scm-hub-inventory-move-flow`(매입+위탁 한 원장).
