# Scale-to-zero 무료 전환 런북 (2026-06-28)

목표: 안 쓸 땐 인스턴스 0개(월 ~0원), Cloud Scheduler가 정해진 시각에 깨워 데이터 갱신.
- 매일 **07:00** 전체 갱신(판매 증분 + 스냅샷 전체)
- 매일 **11:10~23:10 매시 10분** 판매 증분(13회)
- 수동 "판매 갱신" 버튼 제거(프론트), 갱신은 전적으로 Scheduler가 수행.

코드 변경(완료):
- `db.py load_goods_master` → 오프라인 주문 goods만 적재(600만→약 11.5만). 검증: 최근30일 판매 goods 누락 0, 속성 불일치 0.
- `api.py` → `POST /api/cron/refresh?mode=full|sales` 동기·`X-Cron-Token` 보호 엔드포인트.
- `web/src/App.tsx` → 판매 갱신 버튼/핸들러 제거.

배포 URL: https://musinsa-dashboard-573001095666.asia-northeast3.run.app
리전: asia-northeast3 · 서비스: musinsa-dashboard

---

## 0) 업데이트된 소스 업로드 (Cloud Shell)
> ⚠️ Cloud Shell은 **리눅스**다. `cd C:\...` 하면 실패하고 그대로 `~`(홈)에서 배포돼 Dockerfile 없이 Buildpacks로 빌드 실패한다. 반드시 아래처럼 `~/musinsa-dashboard`에서 실행.
> zip은 로컬에서 `make-deploy-zip.ps1`로 생성(제외·정방향슬래시 자동).

**업로드 전** 옛 zip 삭제: `rm -f ~/mok-dashboard-deploy.zip` (안 하면 `…(1).zip` 중복명으로 올라감). 그 뒤 Cloud Shell 우측 상단 **⋮ → Upload** 로 `mok-dashboard-deploy.zip` 업로드(홈 `~`에 올라감). 그 뒤:
```bash
rm -rf ~/musinsa-dashboard                                   # ★ 옛 소스 완전 삭제(unzip -o 만으론 삭제·이름변경된 파일이 남아 스테일 빌드)
unzip -o ~/mok-dashboard-deploy.zip -d ~/musinsa-dashboard
cd ~/musinsa-dashboard
# 변경 반영 확인(마커는 최근 변경 문자열로 교체해 사용)
grep -q "api/cron/refresh" api.py && echo "OK: 코드 반영됨" || echo "FAIL: 업로드/해제 확인 필요"
```

## 1) CRON_TOKEN 시크릿 생성 + 런타임 SA 권한
```bash
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create cron-token --data-file=-

PNUM=$(gcloud projects describe "$(gcloud config get-value project)" --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding cron-token \
  --member="serviceAccount:${PNUM}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor
```

## 2) scale-to-zero 재배포 (코드 갱신 + 작은 인스턴스 + CRON_TOKEN 추가)
`--update-secrets`는 기존 시크릿(auth/databricks)·GCS 볼륨·env를 보존하고 CRON_TOKEN만 추가한다.
```bash
cd ~/musinsa-dashboard
gcloud run deploy musinsa-dashboard \
  --source . \
  --region=asia-northeast3 \
  --min-instances=0 \
  --max-instances=1 \
  --memory=4Gi \
  --cpu=1 \
  --timeout=1800 \
  --cpu-throttling \
  --update-secrets=CRON_TOKEN=cron-token:latest
```

## 3) Cloud Scheduler 잡 2개
```bash
URL="https://musinsa-dashboard-573001095666.asia-northeast3.run.app/api/cron/refresh"
TOKEN=$(gcloud secrets versions access latest --secret=cron-token)

# 매일 07:00 — 전체 갱신
gcloud scheduler jobs create http mok-refresh-full \
  --location=asia-northeast3 \
  --schedule="0 7 * * *" --time-zone="Asia/Seoul" \
  --uri="${URL}?mode=full" --http-method=POST \
  --headers="X-Cron-Token=${TOKEN}" \
  --attempt-deadline=1800s

# 매일 11:10~23:10 매시 10분 — 판매 증분
gcloud scheduler jobs create http mok-refresh-sales \
  --location=asia-northeast3 \
  --schedule="10 11-23 * * *" --time-zone="Asia/Seoul" \
  --uri="${URL}?mode=sales" --http-method=POST \
  --headers="X-Cron-Token=${TOKEN}" \
  --attempt-deadline=1800s
```
※ `asia-northeast3` 에서 Scheduler가 "location not supported" 나면 `--location=asia-northeast1` 로 두 잡 모두 변경.

## 4) 동작 확인
```bash
# cron 엔드포인트 직접 테스트(판매 증분) — {"ok":true,...} 나오면 성공
TOKEN=$(gcloud secrets versions access latest --secret=cron-token)
curl -s -X POST -H "X-Cron-Token: ${TOKEN}" \
  "https://musinsa-dashboard-573001095666.asia-northeast3.run.app/api/cron/refresh?mode=sales"

# goods_master 스냅샷을 작은 버전으로 즉시 교체하려면 1회 전체 갱신
curl -s -X POST -H "X-Cron-Token: ${TOKEN}" \
  "https://musinsa-dashboard-573001095666.asia-northeast3.run.app/api/cron/refresh?mode=full"

# 스케줄러 수동 트리거 테스트
gcloud scheduler jobs run mok-refresh-sales --location=asia-northeast3
```

## 비용
scale-to-zero는 요청/갱신 처리 시간만 과금. 일 사용량(갱신 13~14회 + 조회 수십 분)은 Cloud Run
무료 한도(월 vCPU 180k초 / 메모리 360k GiB초 / 요청 2M) 안에 들어와 **사실상 0원** 수준.
(보호장치: `--min-instances=0`, 토큰 미설정 시 cron 비활성=fail-closed.)
