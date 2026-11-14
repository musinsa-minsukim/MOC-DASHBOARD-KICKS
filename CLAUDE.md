# MOC-DASHBOARD-KICKS — 팀 공용 Claude 허브

이 레포는 무신사 오프라인(킥스 포함) 대시보드 코드이자, **팀원 누구나 Claude 로 원천 데이터를 조회하는 방법**을 모아둔 허브다.
레포에는 **데이터 자체를 두지 않는다** — 매출·재고 숫자는 매번 Databricks 에서 최신으로 조회한다.

## 1. 조회 스킬 (`.claude/skills/`)

이 폴더에서 Claude Code 를 열면 아래 스킬이 자동으로 잡힌다. **새 데이터 영역을 건드리기 전에 스킬부터 읽는다** —
원천 테이블을 직접 탐색하는 건 스킬에 없다는 걸 확인한 다음이다 (`grep -rl "<키워드>" .claude/skills`).

| 스킬 | 도메인 | 뽑는 값 | 주요 원천 |
|---|---|---|---|
| `musinsa-offline-sales` | 오프라인 판매(MOSS) | 순판매수량·GMV(주문−환불)·영수증·객단가·국가별 GMV | `ocmp.moss.order_master`/`order_option`/`claim` |
| `musinsa-online-sales` | 온라인 판매(무신사닷컴) | 일별 순매출·판매수량 | `summary_goods_orders_daily_10`, `order_group.order_opt` |
| `musinsa-store-stock` | 매장·센터 재고(매입+위탁 통합) | 거점별 보유·실가용(할당 제외)·온라인 판매 제외 | `team.scm.stock`(통합 원장), `team.scm.mst_sku_mapping_uid_option_code` |
| `musinsa-store-footfall` | 매장 입객 | 매장×일 방문자수 | `team.sales.retail_editorialshop_visitors_v` |
| `musinsa-goods-master` | 상품 마스터 + 온라인가 + 이미지·링크 | 상품명·브랜드·스타일No·정상가·판매가·쿠폰가·이미지 URL | `musinsa.bizest.goods`, `itgg.goods_sale_price_changes` |
| `musinsa-store-master` | 매장코드 크로스워크 | shop_no ↔ SCM-HUB storage_id ↔ ERP plant·lgort | `team.commercepm.offline_shopno_storageid`, `ocmp.moss.shop_location` |
| `erp-inventory-move-flow` | 매입 재고이동(STO) | 출고확정前/출고확정/이동중/매장입고확정 | `pbo.moms`, `musinsa.stock` |
| `scm-hub-inventory-move-flow` | 위탁 재고이동(SCM-HUB) | 위 4단계 + 매장출고·직납 | `ocmp.scm_hub` |
| `inventory-move-flow-all` | 매입+위탁 통합 이동 | 위 4단계를 구분 열과 함께 통합 | (위 두 개 통합) |
| `musinsa-stock-flow-status` | 재고 흐름 상태값(파트너사→물류→매장→반납) | 입고 신청·하차 완료 / 센터 가용·할당·출고작업중·패킹완료 / 온라인 판매 제외 / 매장 입고 예정 5단계 / 반납 3단계 | `pbo.logistics`(MWMS), `ocmp.scm_hub`, `team.scm.stock` |

대시보드 화면이 쓰는 쿼리 전체 목록은 `DATABRICKS-QUERIES.md`, 실제 로직은 `db.py`.

## 2. 실행 경로 (둘 중 하나)

1. **Databricks MCP `execute_sql`** — Claude 가 바로 SQL 을 실행. 설정은 아래 4절.
2. **로컬 러너 `tools/dbx.py`** — MCP 가 끊기거나 `PENDING` 만 돌려줄 때(자주 있음) 쓰는 대체 경로.
   ```bash
   python tools/dbx.py q.sql                 # 표로 출력
   python tools/dbx.py q.sql --csv out.csv   # 엑셀 호환 CSV
   python tools/dbx.py -e "SELECT current_date()"
   ```
   의존성: `pip install -r requirements.txt` (또는 `uv sync`). Python 3.11+.

## 3. 반드시 지키는 데이터 규칙

- **판매 검증 불변식**: 오프라인 판매 로직을 바꾸면 `python tools/dbx.py tools/verify_moss.sql` → `PASS` 여야 한다.
  2026-06-21 순매출 **765,922,600원 / 순판매수량 20,649개** (영수증수 아님, 오프라인 4종 매장 필터 + `shop_no` 19 제외).
- **오프라인 판매 정의**: `dummy_order = 0 AND order_status = 50`, 판매일 = `COALESCE(transaction_at, created_at)`,
  순판매 = 주문(+1) − 환불(−1, `claim_type='REFUND'`, 환불처리일 기준). 교환은 제외.
- **매장 필터 누락 주의**: 매장 조인 없이 MOSS 를 집계하면 무신사 스탠다드·29CM 까지 잡혀 숫자가 약 3배가 된다.
- **MOSS 적재 주기**: 매시 27분 배치, 직전 시각까지만 반영(실시간 아님). 당일 첫 판매는 11:30경.
  "오늘 vs 전주 동시간대" 비교는 당일 전사 `MAX(hour)` 로 양쪽을 같이 자른다.
- **매입/위탁 코드**: `business_model_type` / `hist_goods_type` 은 **S = 매입(ERP), P = 위탁** (알파벳과 반대라 헷갈림 주의).
  매입 재고는 ERP, 위탁 재고는 SCM-HUB(MFS).
- **불량재고**: `goods_summary.bad_kqty` 사용(`bad_qty` 는 미사용). 매입은 `AND kqty > 0` 없으면 약 5배로 부풀려진다.
- **CDC 테이블**(goods_summary/product/itgg.goods 등)은 키별 최신·비삭제 행만 취한다.
- **ERP 매장 코드**는 (plant, lgort) 쌍으로 쓴다. lgort 단독 필터 금지.
- SQL 작성: `goods_no` 는 `CAST(... AS BIGINT)`, 한글 컬럼 별칭엔 백틱.
- **공용 웨어하우스**(Shared SQL Warehouse, 최대 5클러스터)라 붐비면 수십 분 대기할 수 있다.
  조회를 짧은 제한시간으로 끊고 재시도하면 대기열 맨 뒤로 간다. 무거운 조회는 한가한 시간에.

## 4. 팀원 초기 설정 (1회)

1. **Databricks 개인 토큰(PAT)** 발급: 워크스페이스 `https://musinsa-data-ws.cloud.databricks.com` → User Settings → Developer → Access tokens.
   SQL 웨어하우스 `c0ee970a9c3ed562` 사용 권한이 필요하다(없으면 데이터팀에 요청).
   **토큰은 채팅·커밋·슬랙에 절대 붙여넣지 않는다.** 노출되면 즉시 폐기하고 재발급.
2. **로컬 러너용** `.streamlit/secrets.toml` (`.gitignore` 됨):
   ```toml
   [databricks]
   host = "musinsa-data-ws.cloud.databricks.com"
   http_path = "/sql/1.0/warehouses/c0ee970a9c3ed562"
   token = "<본인 PAT>"
   ```
3. **Databricks MCP**(선택): 사용자 설정 `~/.claude.json` 의 `mcpServers` 에 추가 후 Claude Code 재시작.
   ```json
   "databricks": {
     "command": "uvx",
     "args": ["--with", "mcp<2", "databricks-mcp-server@0.4.4"],
     "env": {
       "DATABRICKS_HOST": "https://musinsa-data-ws.cloud.databricks.com",
       "DATABRICKS_TOKEN": "<본인 PAT>",
       "DATABRICKS_WAREHOUSE_ID": "c0ee970a9c3ed562",
       "UV_SYSTEM_CERTS": "1"
     }
   }
   ```
   - 버전을 고정한다: `@latest` 는 `mcp 2.x` 를 받아 기동 즉시 죽는다(`CONNECTION_CLOSED` 로만 보임).
   - 서버가 작업 폴더에 `databricks_mcp.log` 를 쓰므로, 쓰기 가능한 폴더로 `cd` 하는 래퍼 스크립트로 띄우면 안정적이다.
   - `UV_SYSTEM_CERTS=1` 은 사내 프록시 TLS 통과용.

## 5. 레포 규칙

- **데이터·시크릿 커밋 금지**: csv/parquet/xlsx 결과물, `cache/`, `secrets.toml`, `.env*`, `auth_config.yaml`.
- 조회 결과물은 레포 밖(개인 폴더)에 저장한다.
- 스킬 정의가 틀린 걸 발견하면 **스킬 파일을 고쳐 PR** 로 올린다 — 팀 전체가 같은 정의를 쓰게 하는 게 이 허브의 목적이다.
- 대시보드에 기능을 추가할 때 기존 요소(특히 합계행)를 없애지 않는다.
- 배포·운영은 `DEPLOY-gcp.md`, `DEPLOY-scale-to-zero.md`, `GITHUB-SETUP.md` 참고.
