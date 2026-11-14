---
name: musinsa-store-stock
description: >-
  Look up MUSINSA stock by store(매장) and by center/hub(센터·허브) from Databricks — 매입(1P/PB)+위탁(3P)
  통합. 2026-11 신규 통합원장 team.scm.stock 한 테이블로 goods_no·매장·센터별 보유/실가용 재고를 가볍게 뽑는다
  (SLS·IVL 모델). Use whenever the user asks 점별/매장별 재고, 창고·센터·허브 재고, goods_no의 매장·센터 재고,
  특정 매장 보유재고, 매입/위탁 통합 재고. 허브→매장 이동(STO)흐름은 inventory-move-flow 스킬군, 상품속성은 musinsa-goods-master.
---

# 무신사 재고 (매입·위탁 통합) — 조회 스킬

매장(STORE)·센터(FC) 재고를 **신규 통합 원장 `team.scm.stock` 한 테이블**로 조회한다.
매입(1P/PB)+위탁(3P)이 한 테이블에 통합돼, 기존 5원천(editorial/raw_erp/mfs/sap) 조인이 불필요 — **가볍다**.

- 실행: Databricks MCP `mcp__databricks-mcp__execute_sql` (없으면 `ToolSearch "select:mcp__databricks-mcp__execute_sql"` 로 로드)
- 원천: **`team.scm.stock`** (매시 시점, SLS·IVL 기반 현재고). 상세 모델 → 메모리 `musinsa-sls-ivl-stock-model`.
- 공통키 **`sku_id`** · 거점축 **`root_node_id`**.

## 핵심 정의 (2026-11-11 실측 검증 완료)
- **namespace**: `STORE`(매장 117곳) / `FC`(온라인 물류센터 20곳: 여주·안성·이천·동탄·파주·인천…) / VIRTUAL / FC_3PL.
- **매장 크로스워크 = 항등식**: STORE `root_node_id`(문자) == 기존 `shop_no`(int). `CAST(root_node_id AS INT)`만. (FC는 문자코드 NWH01/ANS01…)
- **수량 컬럼**: `total_quantity`=보유(물리) **= 기존 editorial sellable_qty / raw_erp wqty** (매장 goods단위 diff 0, FC 매입 ~95% 검증). `remaining_quantity`=진할당+가할당 뺀 **실가용**(STORE는 대개 total=remaining). `online_except_quantity`=온라인 판매제외(FC만 ≠0). 온라인판매가능(ATS)은 `team.scm.stock_snapshot_daily.online_available_quantity`에만.
- **재고 항등식**: `total = remaining + allocation_quantity(진할당) + virtual_allocation_quantity(가할당)`. 실물은 `total` 하나만 봄.
- **매입/위탁 구분은 재고테이블에 없음** → `goods_no → partnerportal.company.margin_type`(WONGA=매입/FEE=위탁). 한 브랜드가 매입·위탁 혼재 가능.
- **상품 연결 = SKU 매핑**: `team.scm.mst_sku_mapping_uid_option_code` (sku_id ↔ **uid=goods_no**, option_code). 수량 집계 땐 **`platform='MUSINSA' AND is_rep_uid_option_code_musinsa = TRUE`** 로 SKU당 1행(아래 매핑 대표 플래그 참고). FC 매핑 커버리지 96.4%.

- **매핑 대표 플래그(2026-11-12 17시 변경, 데이터 담당 공지)**: 열 이름이 `is_rep_uid` → `is_rep_uid_option_code`, `is_rep_uid_musinsa` → `is_rep_uid_option_code_musinsa`, `is_rep_uid_cm29` → `is_rep_uid_option_code_cm29` 로 바뀌었다. 대표 기준이 "SKU당 대표 uid"에서 "SKU당 대표 (uid, option_code) 한 쌍"으로 바뀐 것. 옛 이름을 쓰면 `UNRESOLVED_COLUMN` 으로 실패한다.
  - **무신사 상품번호(goods_no)를 붙일 땐 `platform='MUSINSA' AND is_rep_uid_option_code_musinsa = TRUE`** → SKU당 정확히 1행(2026-11-13 검증: 2,205,689 SKU, 중복 0).
  - `is_rep_uid_option_code` 만 걸어도 SKU당 1행이지만(2,321,236 SKU) 그중 122,313개는 대표가 29CM 상품번호다 → 무신사 상품번호가 필요하면 쓰지 말 것.
  - 일별 이력 `team.scm.mst_sku_mapping_uid_option_code_snapshot` 은 2026-10-16~11-12 이력이 지워지고 11-13부터 새 열로 다시 쌓인다(11-13 저녁 조회 시 테이블 없음).

## 표준 쿼리 A — 통합 재고 (매장+센터, 매입+위탁) [goods_no·매장 기준] ★주력

```sql
SELECT
    s.namespace,                                                    -- STORE(매장)/FC(센터)
    CASE WHEN s.namespace='STORE' THEN CAST(s.root_node_id AS INT) END AS shop_no,  -- 매장=shop_no
    s.node_name,
    m.uid                                   AS goods_no,
    CASE c.margin_type WHEN 'WONGA' THEN '매입' WHEN 'FEE' THEN '위탁' ELSE '기타' END AS `구분`,
    CAST(SUM(s.total_quantity)     AS BIGINT) AS `보유재고`,          -- = 기존 sellable
    CAST(SUM(s.remaining_quantity) AS BIGINT) AS `실가용_할당제외`,
    CAST(SUM(s.online_except_quantity) AS BIGINT) AS `온라인제외`
FROM team.scm.stock s
JOIN team.scm.mst_sku_mapping_uid_option_code m
  ON m.sku_id = s.sku_id AND m.platform='MUSINSA' AND m.is_rep_uid_option_code_musinsa = TRUE
LEFT JOIN musinsa.bizest.goods g          ON CAST(g.goods_no AS BIGINT) = m.uid
LEFT JOIN musinsa.partnerportal.company c ON c.com_id = g.com_id
WHERE s.namespace IN ('STORE','FC')
  {{FILTER}}
GROUP BY s.namespace, s.root_node_id, s.node_name, m.uid, c.margin_type
ORDER BY goods_no, s.namespace, `보유재고` DESC;
```

### 필터 사용법 (`{{FILTER}}`)
- **특정 상품**: `AND m.uid IN (4841258, 6594252)`
- **특정 매장**: `AND s.root_node_id = '88'` (88=메가성수. `musinsa-store-master`로 매장명↔shop_no)
- **매장만/센터만**: WHERE의 `namespace IN (...)`를 `='STORE'` 또는 `='FC'`로
- **매입만/위탁만**: `AND c.margin_type = 'WONGA'`(매입) / `'FEE'`(위탁)
- **상품 단위 합계**: node 컬럼 빼고 `m.uid`로만 GROUP BY

### 변형
- **일별 이력·수불·입출고예정**이 필요하면 `team.scm.stock_snapshot_daily`(+`snapshot_date` 파티션 WHERE 필수, +online_available/expected_out_b2c·b2b/expected_in/sale·inbound·outbound…).
- **바코드·배치까지** 필요하면 `team.scm.stock_detail`(leaf·batch·barcode).

## 규칙
- 한글 컬럼 별칭 **백틱** 필수. goods_no는 `CAST(... AS BIGINT)`, uid(mapping)는 이미 bigint.
- 수량 합계 시 매핑은 **반드시 `platform='MUSINSA' AND is_rep_uid_option_code_musinsa=TRUE`**(안 걸면 1:N로 부풀림. 옛 열 이름 `is_rep_uid_musinsa` 는 없어짐). 내 테이블 LEFT JOIN 유지(미매핑 SKU 안 빠지게).
- 상품명/브랜드는 goods_no로 `musinsa-goods-master` 보강. 매장명↔shop_no↔ERP/SCM코드는 `musinsa-store-master`.
- 스냅샷(현재 보유)만. **허브→매장 이동 진행(출고확정前/이동중/입고확정)**은 `scm-hub-inventory-move-flow`(매입+위탁 한 원장) 또는 단계 전체는 `musinsa-stock-flow-status`.
- 파티션: `team.scm.stock`은 파티션 없음. `*_snapshot_daily`는 `snapshot_date` WHERE 필수.

---

## (레거시 폴백) 구 5원천 — 이관 전/대사용으로만

신규 `team.scm.stock`로 전환됨. 아래는 **2026-11 이전 로직**이라 과거 대사·검증용으로만 쓴다(평소엔 위 A 사용). 매입이 SLS로 이관되기 전엔 매입 재고가 `team.scm.stock`에 없었음 → 그 시점 데이터엔 레거시 필요.
- 점재고: `team.sales.dsh_d_upt_editorial_stock_summary`(파티션키 `ord_state_date_st`), sellable_qty. shop_no=root_node_id.
- 허브 매입: `team.partner.raw_erp_stock`(파티션 `dt`) 허브1000(store_cd='1000', lgort IN '2000','2010','2020','2060')+허브1700(store_cd='1700', lgort='2000'), wqty. ⚠️ lgort `LIKE '20%'` 금지(과대계상).
- 허브 위탁: `team.commercepm.mfs_stock_daily`(날짜컬럼 없음, 현재스냅샷) ⋈ `team.scm.scm_hub_goods_meta`(sku_id→supplier_barcode, rn=1 최신).
- 트레이딩 매장: `ocmp.moss.sap_inventory`(plant 1700) ⋈ `ocmp.moss.shop_location`.
- 대사 검증(2026-11-11): 메가성수 goods단위 sellable=total diff 0 / 매입 허브 20.20M→FC 21.49M(~95%) / 위탁 MFS 1.90M→FC 2.57M.
