---
name: erp-inventory-move-flow
description: >-
  ERP 재고이동플로우 — 무신사 매입(ERP/SAP) 상품의 허브→매장 재고이동(STO)을 단계별
  수량으로 조회한다. goods_no × 사이즈별로 출고확정前 / 출고확정 / 이동중 / 매장입고확정
  4단계를 한 번에 뽑는다. 사용자가 특정 매장(예 3058 고양 킥스)으로의 이동지시/출고/입고
  현황, A-번호(이동지시번호) 진행상태, STO 재고이동 플로우, 매장 입고 예정/확정 수량을
  물으면 사용한다. Databricks(pbo.moms + musinsa.stock + musinsa.itgg) 기반.
---

# ERP 재고이동플로우 (매장 STO 이동)

무신사 **매입(ERP/SAP)** 상품이 허브/물류센터에서 매장으로 이동(STO)하는 흐름을 단계별 수량으로 조회하는 스킬.
`goods_no × 사이즈` 기준으로 **출고확정前 → 출고확정 → 이동중 → 매장입고확정** 4단계를 뽑는다.

- 실행: Databricks MCP `mcp__databricks__execute_sql` (없으면 `ToolSearch "select:mcp__databricks__execute_sql"` 로 로드)
- 위탁(SCM-HUB) 이동은 별도 파이프라인(ocmp.scm_hub / team.scm)이며 이 스킬은 **매입(ERP)** STO 전용.

## 4단계 정의 & 원천

| 단계 | 뜻 | 원천 테이블 | 필터 |
|---|---|---|---|
| **출고확정前** | 이동지시 원 수량(baseline, 반출·입고돼도 **유지**) | `pbo.moms.iif_sap_sto` | `MSTAT='5'`, `BUDAT` 빈값 |
| **출고확정** | 실제 반출(GI)된 누적 수량 | `pbo.moms.oif_sap_str` | `BWART='313'` (TF 반출→저장장소) |
| **매장입고확정** | 매장 저장위치 배치(GR)된 수량 | `musinsa.stock.erp_inout_bound` | `bwart='315'`, **`shkzg='S'`** (입고 다리) |
| **이동중** | 파생: `출고확정 − 매장입고확정` | (계산) | — |

핵심 규칙(검증됨):
- **출고확정前은 baseline이라 감산하지 않는다.** `iif_sap_sto`는 반출돼도 행을 유지하므로 그대로 SUM. (출고확정前 ≥ 출고확정 ≥ 매장입고확정 순으로 비교 가능)
- **`oif_sap_str`(313 반출)와 `erp_inout_bound`(313, shkzg=S)는 동일 이벤트**(MVNO+MVITEM+BARCODE 100% 일치). 출고확정은 둘 중 하나만 쓴다. 313은 **반출=출고**이지 입고가 아님.
- **매장입고확정은 `erp_inout_bound` `bwart='315' AND shkzg='S'`**(315=매장 저장위치 배치, S=입고 다리). 초도/미입고 매장은 315=0건 → 매장입고확정 0이 정상.
  - ⚠️ **절대 `mvno LIKE 'A%'`로 걸지 말 것**: 315 레코드는 `mvno`가 **항상 NULL**(A-번호는 313 반출에만 붙음)이라, 이 조건을 넣으면 315가 전부 탈락해 **매장입고확정이 항상 0**이 된다. (실측: 전체 315 1,400만 건 중 mvno 비-NULL 0건.) 과거 이 버그가 있었고 초도 미입고 매장에선 0이 정상처럼 보여 안 드러났다 — 실입고 발생 시 노출됨(예: 5901579 @ 3058).
  - ⚠️ 315는 입고(`shkzg='S'`)와 전송분(`shkzg='H'`) 두 다리가 **둘 다 양수 menge**라 `shkzg='S'` 없이 합치면 이중집계된다. 반드시 `shkzg='S'`.
- 세 소스 모두 라이브(실시간). 과거 `team.partner.raw_moms_iif_sap_sto`는 2025-03 정지된 죽은 피드이므로 쓰지 말 것.

## goods_no·사이즈 매핑 (★ 조인 불필요)

STO 테이블에 이미 들어있다. **절대 item_id/barcode로 조인하지 말 것.**
- `S_MATNR`(erp는 `zz_smatnr`) = **goods_no** (= `musinsa.itgg.goods.goods_id`, 100% 매칭)
- `OPTION`(erp는 `zz_option`) = **사이즈명**
- ⚠️ 함정: `S_MATNR`을 item_id로 오인해 `goods_summary`/`goods_barcode_history`에 조인하면 goods_no와 item_id 값 도메인이 겹쳐 **엉뚱한 상품**으로 물린다(예: 3719998은 goods_no=뉴발란스지만 item_id로 보면 1142454 비에스큐티로 오매핑). 바코드도 재사용 이슈로 금지.
- 상품명·브랜드명만 goods_no → `musinsa.itgg.goods`(`goods_name`, `brand_name`, 최신행) 조인.

## 매장 코드 = ★ (WERKS 플랜트, LGORT) **쌍**

⚠️ **lgort는 플랜트별이라 단독 필터하면 다른 매장이 섞인다.** 예: `3360` = 플랜트 **1000이면 "MSS 무신사 스토어 성수"(66,918라인)**, **1700이면 "스타필드_고양"(259)**. 반드시 `WERKS`와 함께 건다.

또한 **한 매장이 플랜트마다 다른 lgort**를 갖는다 → **1000·1700 둘 다 넣어야 누락 없음**.

| 매장 | WERKS 1000 | WERKS 1700 |
|---|---|---|
| 무신사 킥스 스타필드 고양점 | **3058** | **3053** |
| 스탠다드 스타필드 고양 | 3500 | — |
| 스타필드_고양 | — | 3360 |

권장 필터(파라미터 1개로 관리):
```sql
CONCAT(WERKS,'-',GR_LGORT) IN ('1000-3058','1700-3053')
```

매장 코드 조회:
```sql
SELECT WERKS, GR_LGORT, GR_LGOBE, COUNT(*) FROM pbo.moms.iif_sap_sto
WHERE GR_LGOBE LIKE '%<매장명>%' GROUP BY 1,2,3 ORDER BY 1,2;
```

⚠️ `erp_inout_bound`(③ 315)에는 **플랜트 컬럼이 없어** `lgort IN (...)` 로만 필터 가능. 3058/3053은 lgort 충돌 없어 안전. lgort가 플랜트 간 충돌하는 매장(예 3360)은 315에 `mvno`가 없어 A-번호로 분리 불가 → 이런 매장의 매장입고확정은 별도 방법(자재문서/바코드 등)이 필요하니 주의.

## 표준 쿼리

`{{STORE_PAIRS}}` = `'1000-3058','1700-3053'` (플랜트-lgort 쌍, 1000·1700 모두), `{{STORE_LGORTS}}` = `'3058','3053'` 로 교체. (아래는 고양 킥스 예시)

```sql
WITH
ev AS (
  -- ① 출고확정 前 (원 지시수량 baseline, 유지)
  SELECT '1' AS stg, NULLIF(S_MATNR,'') AS goods_no, OPTION AS opt, CAST(MENGE AS DOUBLE) AS qty
  FROM pbo.moms.iif_sap_sto
  WHERE CONCAT(WERKS,'-',GR_LGORT) IN ({{STORE_PAIRS}})   -- 예 '1000-3058','1700-3053'
    AND MSTAT='5' AND (CANC IS NULL OR CANC='')
    AND (BUDAT IS NULL OR BUDAT IN ('','00000000'))
  UNION ALL
  -- ② 출고확정 (반출/GI, 누적)
  SELECT '2', NULLIF(S_MATNR,''), OPTION, CAST(MENGE AS DOUBLE)
  FROM pbo.moms.oif_sap_str
  WHERE CONCAT(WERKS,'-',GR_LGORT) IN ({{STORE_PAIRS}})
    AND BWART='313' AND (CANC IS NULL OR CANC='')
  UNION ALL
  -- ③ 매장 입고확정 (315 배치/GR, 입고 다리 shkzg='S') — 이 테이블엔 플랜트 없음 → lgort로만
  --    ⚠️ mvno LIKE 'A%' 쓰지 말 것: 315는 mvno가 항상 NULL이라 입고확정이 0이 됨
  SELECT '3', NULLIF(zz_smatnr,''), zz_option, CAST(menge AS DOUBLE)
  FROM musinsa.stock.erp_inout_bound
  WHERE lgort IN ({{STORE_LGORTS}}) AND bwart='315' AND shkzg='S'
),
g AS (
  SELECT goods_id, goods_name, brand_name FROM (
    SELECT goods_id, goods_name, brand_name,
      ROW_NUMBER() OVER (PARTITION BY goods_id
        ORDER BY (CASE WHEN Op='delete' THEN 1 ELSE 0 END), tr_ts DESC, updated_at DESC) rn
    FROM musinsa.itgg.goods
  ) WHERE rn=1
)
SELECT e.goods_no,
       g.brand_name AS `브랜드명`, g.goods_name AS `상품명`, e.opt AS `사이즈`,
       SUM(CASE WHEN e.stg='1' THEN e.qty ELSE 0 END) AS `출고확정前`,
       SUM(CASE WHEN e.stg='2' THEN e.qty ELSE 0 END) AS `출고확정`,
       SUM(CASE WHEN e.stg='2' THEN e.qty ELSE 0 END)
         - SUM(CASE WHEN e.stg='3' THEN e.qty ELSE 0 END) AS `이동중`,
       SUM(CASE WHEN e.stg='3' THEN e.qty ELSE 0 END) AS `매장입고확정`
FROM ev e
LEFT JOIN g ON CAST(g.goods_id AS STRING) = e.goods_no
GROUP BY e.goods_no, g.brand_name, g.goods_name, e.opt
ORDER BY `출고확정前` DESC, `출고확정` DESC;
```

컬럼 순서는 항상 **출고확정前 / 출고확정 / 이동중 / 매장입고확정**.

## 변형

- **A-번호(이동지시번호) drill-down**: ①②블록(`iif`/`oif`)에 `MVNO`를 추가. ⚠️ ③(315)는 `mvno`가 NULL이라 A-번호로 못 나눈다 → A-번호별 매장입고확정은 이 테이블만으로 불가(전체 매장 기준 합만 가능).
- **특정 A-번호만**: ①②에 `AND MVNO='A0000xxxxx'` 추가. ③은 A-번호 필터 불가(위와 동일 이유) — 매장입고확정을 A-번호 단위로 원하면 자재문서/바코드 매칭 등 별도 로직 필요.
- **매장 전체 합계**: 최종 SELECT를 `SUM(...)`만 남기고 GROUP BY 제거.
- **고양 3매장 통합**: `GR_LGORT`/`lgort` 를 `IN ('3058','3500','3360')` 로 바꾸고 매장 구분 컬럼(`GR_LGOBE`) 추가.

## 규칙
- 한글 컬럼 별칭은 **백틱** 필수(`` `출고확정前` ``). 아니면 `INVALID_IDENTIFIER` 에러.
- `MSTAT='5'` 는 iif/oif 에만 적용(허브·센터→매장). erp_inout_bound 에는 MSTAT 없음.
- 매장입고확정이 0이면 "아직 매장 미입고"(초도 반출 단계)로 해석 — 오류 아님.
- 관련 배경/함정은 메모리 `inventory-move-confirmed-recipe`, `stock-business-model-type-code` 참조.
