# MOC-DASHBOARD-KICKS — 무신사 오프라인 대시보드 + 팀 공용 조회 허브

무신사 오프라인(킥스 포함) 매장의 **판매·재고 대시보드 코드**이자,
팀원 누구나 **원천 데이터를 같은 정의로 조회**하기 위한 허브다.

> **레포에 데이터는 두지 않는다.** 매출·재고 숫자는 매번 Databricks 에서 최신으로 조회한다.
> csv/parquet/xlsx 결과물, `cache/`, `secrets.toml`, `.env*`, `auth_config.yaml` 은 커밋 금지.

---

## 1. 데이터를 조회하려면 — 여기부터 읽는다

| 먼저 볼 것 | 내용 |
|---|---|
| **[CLAUDE.md](CLAUDE.md)** | **시작점.** 조회 스킬 9종 목록, 반드시 지키는 데이터 규칙, 팀원 1회 설정(Databricks PAT·MCP) |
| [`.claude/skills/`](.claude/skills) | 도메인별 조회 스킬 — 검증된 정의와 표준 SQL 이 들어 있다 |
| [DATABRICKS-QUERIES.md](DATABRICKS-QUERIES.md) | 대시보드 화면이 실제로 쓰는 쿼리 전체 목록 |

이 폴더에서 Claude Code 를 열면 `.claude/skills/` 가 자동으로 잡힌다.
**새 데이터 영역을 건드리기 전에 스킬부터 읽는다** — 원천 테이블을 직접 뒤지는 건
그 주제가 스킬에 없다는 걸 확인한 다음이다 (`grep -rl "<키워드>" .claude/skills`).

### 조회 스킬 (요약 — 상세는 CLAUDE.md)

| 스킬 | 도메인 |
|---|---|
| `musinsa-offline-sales` | 오프라인 판매(MOSS) — 순판매수량·GMV·객단가·국가별 |
| `musinsa-online-sales` | 온라인 판매(무신사닷컴) |
| `musinsa-store-stock` | 매장·센터 재고 (매입+위탁 통합 원장) |
| `musinsa-stock-flow-status` | 재고 흐름 상태값 (파트너사 → 물류 → 매장 → 반납) |
| `scm-hub-inventory-move-flow` | 재고이동 4단계 (매입+위탁, SCM-HUB) |
| `musinsa-store-master` | 매장코드 크로스워크 |
| `musinsa-goods-master` | 상품 마스터 + 온라인가 + 이미지·링크 |
| `musinsa-store-footfall` | 매장 입객 |

> ⚠️ **ERP(SAP)는 재고·이동 조회에서 뺐다 (2026-11-14).**
> 보유 재고는 통합 원장 `team.scm.stock`, 이동은 `ocmp.scm_hub` 하나로 본다.
> 매입 이동도 SCM-HUB 에 적재되므로 ERP 를 함께 더하면 **매입이 이중 집계**된다.
> 구 `erp-inventory-move-flow` / `inventory-move-flow-all` 스킬은 이 이유로 삭제했다(필요하면 git 이력).

### 실행 경로

1. **Databricks MCP `execute_sql`** — Claude 가 바로 SQL 실행 (설정은 CLAUDE.md 4절)
2. **로컬 러너 `tools/dbx.py`** — MCP 가 끊기거나 `PENDING` 만 돌려줄 때

```bash
python tools/dbx.py q.sql                 # 표로 출력
python tools/dbx.py q.sql --csv out.csv   # 엑셀 호환 CSV
python tools/dbx.py -e "SELECT current_date()"
```

---

## 2. 대시보드

React(AG Grid) + FastAPI + DuckDB(GCS parquet 캐시), GCP Cloud Run scale-to-zero 배포.
Databricks 는 **스냅샷 갱신 때만** 조회하고, 화면 요청은 parquet 캐시에서 처리한다.

| 파일 | 역할 |
|---|---|
| `serve.py` / `api.py` | 운영 엔트리포인트 (Cloud Run 이 실행하는 것) |
| `db.py` | Databricks 접속 + 모든 원천 쿼리 |
| `store.py` | parquet 스냅샷 저장소 (원자적 교체) / `refresh.py` 갱신 CLI |
| `invtab.py` · `drill.py` · `cmptab.py` · `daily.py` · `floor.py` | 탭별 집계 |
| `web/src/` | 화면 (Inventory / Dashboard / Summary / Compare / Ips …) |
| `app.py` | **구 Streamlit 화면 — 운영 미사용.** 배포는 `serve.py` 만 쓴다 |

### 로컬 실행

```bash
cd web && npm install && npm run build
```

```bash
python serve.py
```

`.streamlit/secrets.toml`(gitignore) 에 Databricks 접속정보가 필요하다 — CLAUDE.md 4절 참고.

### 배포·운영

`main` 푸시 → GitHub Actions(`deploy.yml`, 타입체크·빌드 통과해야 배포).
자세한 건 [DEPLOY-gcp.md](DEPLOY-gcp.md) · [DEPLOY-scale-to-zero.md](DEPLOY-scale-to-zero.md) · [GITHUB-SETUP.md](GITHUB-SETUP.md).

재고 스냅샷은 `refresh-stock.yml`(하루 3회 + 재고 REFRESH 버튼)이 갱신한다.

---

## 3. 레포 규칙

- **데이터·시크릿 커밋 금지** (위 상자 참고). 조회 결과물은 레포 밖 개인 폴더에 둔다.
- **스킬 정의가 틀린 걸 발견하면 스킬 파일을 고쳐 PR** — 팀 전체가 같은 정의를 쓰게 하는 게 이 허브의 목적이다.
- 대시보드에 기능을 추가할 때 기존 요소(특히 합계행)를 없애지 않는다.
- 오프라인 판매 로직을 바꾸면 `python tools/dbx.py tools/verify_moss.sql` → `PASS` 여야 한다.
