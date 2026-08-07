# 무신사 오프라인 매장 판매 대시보드

위탁 + 매입 통합 오프라인 판매 대시보드 (MOSS `offline_sales_master` 기반).
분석 레벨: 매장 / 사업구분(위탁·매입) / 브랜드 / 상품 / 사이즈옵션.

## 구성
- `app.py` — Streamlit 화면 (필터 + KPI + 차트 + 상세표)
- `db.py` — Databricks 접속 + 검증된 통합 fact 로직(`BASE_FACT`) + 집계 쿼리
- `.streamlit/secrets.toml` — 워크하우스 접속정보(토큰) — **git 금지**
- `test_conn.py` / `run_apptest.py` — 접속·렌더 검증 스크립트

## 실행 방법
사전: `uv` 설치됨 (`C:\Users\MUSINSA\.local\bin`).

```powershell
$env:UV_SYSTEM_CERTS = "1"          # 회사 프록시 TLS (Windows 인증서 사용)
cd C:\Users\MUSINSA\musinsa-offline-dashboard
C:\Users\MUSINSA\.local\bin\uv.exe run streamlit run app.py
```
브라우저에서 http://localhost:8501 접속.

## 동작 메모
- 회사 프록시 TLS는 `truststore`(Windows 인증서 저장소)로 통과.
- 데이터 기간: MOSS는 2025-10-16부터. 필터·집계는 모두 Databricks에서 수행 후 작은 결과만 가져옴(캐시 10분/5분).
- 적용 비즈니스 룰: 더미 제외 · 정정(revision) 최신본만 · 환불(−)/교환(0) · GMV=판매가×수량 · 위탁매입=업체코드 기준.

## 다음 단계
1. `dim_store`/`dim_brand`/`fact_sales`를 Databricks 뷰로 만들고, `db.py`의 `BASE_FACT`를 `WITH fact AS (SELECT * FROM <SCHEMA>.fact_sales)` 로 교체(쿼리 단순화).
2. 재고(`ocmp.moss.sap_inventory` + `erp_plant_location`) 추가 → 재고 탭.
3. **Databricks Apps로 배포**(SSO 로그인 + 호스팅 + 토큰 불필요) → 다수 사용자 보안 접근.
