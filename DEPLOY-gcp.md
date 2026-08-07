# GCP Cloud Run 배포 가이드 (무신사 오프라인 대시보드)

Cloud Run = 관리형 컨테이너. **HTTPS 자동**(PWA·폰 접속), **상시 가동(PC 꺼져도 OK)**, **클라우드 자체 자동 갱신**.
`min-instances=1` 인스턴스가 30분마다 판매 증분 + 하루 1회 스냅샷을 **Databricks에서 직접** 당겨옵니다.
→ 그래서 **Cloud Run이 Databricks에 접근**할 수 있어야 하고, 그게 이 문서의 핵심(2·3단계)입니다.

> 실행 위치: **GCP Cloud Shell**(브라우저, gcloud 내장·인증됨 — 설치 0)을 권장. 아래 `<...>` 값만 채우세요.

---

## ⛳ 0. 가장 먼저 — IT/클라우드 관리자 확인 (성패 결정)
Cloud Run은 회사 VPN 밖이라, Databricks가 다음 중 하나여야 합니다:
1. **인터넷에서 토큰으로 접근 가능 + IP 허용목록 방식** → 우리가 만든 **고정 송신 IP(아래 2단계)** 를 IT가 허용목록에 추가하면 됨. ✅ 이 가이드로 진행.
2. **VPN/PrivateLink 전용(인터넷 차단)** → Cloud Run이 닿을 수 없음. 이 경우는 GCP↔사내 VPN 연동(무거움)이거나, "PC가 갱신→GCS 업로드 / Cloud Run은 GCS만 서빙"하는 하이브리드로 전환해야 함.

**IT에게 물어볼 것:** "Databricks 워크스페이스를 *사외 고정 IP(하나)* 로 토큰 접근 허용해줄 수 있나요? (IP access list에 1개 추가)"
- YES → 2단계에서 만든 IP를 전달.
- NO/VPN전용 → 본 가이드 대신 하이브리드(부록) 검토.

---

## 1. 프로젝트·API·코드 준비 (Cloud Shell)
```bash
gcloud config set project <PROJECT_ID>
export REGION=asia-northeast3   # 서울
export VPC=default              # 기본 VPC 사용(전용 VPC가 있으면 그 이름)
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  storage.googleapis.com vpcaccess.googleapis.com compute.googleapis.com
```
**코드 올리기**: 로컬 프로젝트 폴더를 zip으로 만들어(단, `cache/ web/node_modules/ web/dist/ .streamlit/secrets.toml` 제외) Cloud Shell '파일 업로드'로 올리거나, 사내 Git(비공개)에 올려 `git clone`. `auth_config.yaml`은 2-b에서 필요하니 함께 올립니다.

## 2. Cloud Run 고정 송신 IP (← Databricks 허용목록용)
```bash
# (a) Serverless VPC 커넥터
gcloud compute networks vpc-access connectors create dash-conn \
  --region=$REGION --network=$VPC --range=10.8.0.0/28

# (b) 고정 외부 IP 예약
gcloud compute addresses create dash-nat-ip --region=$REGION
export NAT_IP=$(gcloud compute addresses describe dash-nat-ip --region=$REGION --format='value(address)')
echo "★ Databricks 허용목록에 추가할 IP = $NAT_IP"

# (c) Cloud Router + Cloud NAT (이 고정 IP로만 송신)
gcloud compute routers create dash-router --region=$REGION --network=$VPC
gcloud compute routers nats create dash-nat \
  --router=dash-router --region=$REGION \
  --nat-all-subnet-ip-ranges --nat-external-ip-pool=dash-nat-ip
```
→ 출력된 **`$NAT_IP` 를 IT에 전달**해 Databricks IP 허용목록에 등록(0단계 YES인 경우).

## 3. 시크릿·캐시버킷
```bash
# 인증 파일(계정 해시 + 쿠키 서명키) — auth_config.yaml 그대로
gcloud secrets create dash-auth --data-file=auth_config.yaml
# Databricks 토큰 (값은 화면에 안 남게)
printf '%s' '<DATABRICKS_TOKEN>' | gcloud secrets create dbx-token --data-file=-
# parquet 캐시용 GCS 버킷(인스턴스 교체에도 캐시 유지)
export BUCKET=$(gcloud config get-value project)-dash-cache
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access
```

## 4. 배포 (소스 빌드 → Cloud Run, VPC 경유 송신)
```bash
gcloud run deploy musinsa-dashboard \
  --source . --region=$REGION --allow-unauthenticated \
  --min-instances=1 --max-instances=2 \
  --memory=4Gi --cpu=2 --timeout=3600 --concurrency=10 \
  --vpc-connector=dash-conn --vpc-egress=all-traffic \
  --add-volume=name=cache,type=cloud-storage,bucket=$BUCKET \
  --add-volume-mount=volume=cache,mount-path=/mnt/cache \
  --update-secrets=/app/auth_config.yaml=dash-auth:latest,DATABRICKS_TOKEN=dbx-token:latest \
  --set-env-vars=APP_ENV=prod,WAREHOUSE_CACHE=/mnt/cache,AUTO_REFRESH_MINUTES=30,DATABRICKS_HOST=<HOST>,DATABRICKS_HTTP_PATH=<HTTP_PATH>
```
- `--vpc-connector + --vpc-egress=all-traffic`: 모든 송신(=Databricks 조회)이 2단계 고정 IP로 나감.
- `--min-instances=1` + `AUTO_REFRESH_MINUTES=30`: **PC 꺼져도** 클라우드가 30분마다 판매 증분, 하루 1회 스냅샷 자동 갱신.
- 쿠키 서명키는 `auth_config.yaml`(시크릿)에 들어있어 인스턴스가 늘어도 로그인 유지.
- `<HOST>`/`<HTTP_PATH>`: 로컬 `.streamlit/secrets.toml`의 `[databricks] host/http_path` 값.
- 첫 배포 후 **수 분간 데이터 빌드**(판매 2023-10~ 전체 + goods_master). 그동안 API 503 → 끝나면 정상.

## 5. 확인
```bash
gcloud run services describe musinsa-dashboard --region=$REGION --format='value(status.url)'
# → https://musinsa-dashboard-xxxx.a.run.app  (폰에서 접속 + PWA 설치)
```

## 갱신 배포(코드 수정 후)
```bash
gcloud run deploy musinsa-dashboard --source . --region=$REGION   # 재빌드·재배포 (네트워크/시크릿/볼륨 유지)
```

## 보안
- 공개 URL이 부담되면 `--no-allow-unauthenticated` + **IAP**로 사내 구글 SSO 게이트(회사 계정만 접근).
- 공용 비번(kicks0109)은 약함 → 공개 배포 전 비번 강화 권장.
- 토큰·인증파일은 **Secret Manager에만**(이미지/레포 미포함). `.gitignore`로 로컬 시크릿 제외 확인.

---

## 부록) Databricks가 VPN 전용이라 Cloud Run이 못 닿을 때 — 하이브리드
- **갱신만 PC(또는 사내 VM)에서** 수행 → parquet를 `gs://$BUCKET`에 업로드(`gcloud storage rsync cache gs://$BUCKET`).
- Cloud Run은 위 GCS 볼륨을 **읽기 전용**으로 서빙(`AUTO_REFRESH_MINUTES` 제거, Databricks 접근 불필요).
- 단점: 갱신은 그 PC/VM가 돌아야 함(완전 PC-독립은 아님). "PC 꺼져도"를 원하면 **사내에 상시 VM 1대**(이미 Databricks 닿는 네트워크)에서 갱신 스크립트를 cron으로 돌리는 방식이 현실적.
