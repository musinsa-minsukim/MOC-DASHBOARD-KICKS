# GitHub + 자동배포 최초 셋업 (무신사 오프라인 대시보드)

`git push` → GitHub Actions → Cloud Run(`musinsa-dashboard`) 자동배포. **일회성** 설정.
이후 수동 zip 업로드는 불필요. 데이터 갱신(Scheduler)은 지금처럼 그대로 동작.

## 사전값
- GCP 프로젝트: `mok-dashboard-500710` / 리전: `asia-northeast3` / 서비스: `musinsa-dashboard`
- 로컬 프로젝트 `C:\Users\MUSINSA\musinsa-offline-dashboard` 는 이미 git 초기화 + 첫 커밋 완료.

---

## 1) GitHub 저장소 생성 + 첫 푸시 (로컬 Git Bash)
1. GitHub에서 **private** 저장소 생성 (예: `musinsa-offline-dashboard`). README/gitignore 추가 없이 빈 저장소로.
2. 로컬에서 원격 연결 후 푸시:
```bash
cd /c/Users/MUSINSA/musinsa-offline-dashboard
git branch -M main
git remote add origin https://github.com/<<GITHUB_USER>>/<<REPO>>.git
git push -u origin main
```
(로그인 창이 뜨면 GitHub 계정으로 인증. PAT 요구 시 브라우저 흐름을 따르세요.)

## 2) GCP 일회성 — 배포용 서비스계정 + 권한 (Cloud Shell)
```bash
gcloud config set project mok-dashboard-500710
PROJECT=mok-dashboard-500710
PNUM=$(gcloud projects describe $PROJECT --format='value(projectNumber)')

gcloud iam service-accounts create gh-deployer --display-name="GitHub Actions deployer"
SA="gh-deployer@${PROJECT}.iam.gserviceaccount.com"

for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.admin \
            roles/storage.admin roles/iam.serviceAccountUser roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding $PROJECT --member="serviceAccount:$SA" --role=$ROLE
done
```

## 3) Workload Identity Federation (장기 키 없이 GitHub↔GCP 신뢰)
```bash
gcloud iam workload-identity-pools create github-pool --location=global --display-name="GitHub pool"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github-pool --display-name="GitHub provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='<<GITHUB_USER>>/<<REPO>>'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

gcloud iam service-accounts add-iam-policy-binding $SA \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PNUM}/locations/global/workloadIdentityPools/github-pool/attribute.repository/<<GITHUB_USER>>/<<REPO>>"

echo "WIF_PROVIDER=projects/${PNUM}/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
echo "WIF_SERVICE_ACCOUNT=$SA"
```

## 4) GitHub 저장소에 값 등록 (Settings → Secrets and variables → Actions)
**Secrets** (Repository secrets):
- `WIF_PROVIDER` = 위 3)의 출력 경로
- `WIF_SERVICE_ACCOUNT` = `gh-deployer@mok-dashboard-500710.iam.gserviceaccount.com`

**Variables** (Repository variables):
- `GCP_PROJECT` = `mok-dashboard-500710`
- `GCP_REGION` = `asia-northeast3`
- `CLOUD_RUN_SERVICE` = `musinsa-dashboard`

## 5) 첫 자동배포 확인
- 아무 커밋이나 `git push` (또는 GitHub 저장소 **Actions 탭 → Deploy to Cloud Run → Run workflow** 수동 실행).
- Actions 로그 마지막에 `Service [musinsa-dashboard] revision [...] has been deployed` + Service URL이 나오면 성공.
- 이후부터는 코드 고치고 `git push` 만 하면 자동배포. **수동 zip 불필요.**

---
## 참고
- 이 파이프라인은 **코드 배포만** 함. `--source .` 라 기존 서비스 설정(4Gi/CPU1/min0·max1·시크릿·GCS볼륨) 보존 → scale-to-zero 무료 유지.
- **데이터 갱신(mode=full)** 은 별개(Scheduler/수동). mode=full은 메모리가 커서 8Gi/2CPU 필요할 수 있음(DEPLOY 런북 참조) — 자동배포와 무관.
- 시크릿은 절대 커밋 안 됨(`.gitignore`: auth_config.yaml, .streamlit/secrets.toml, INITIAL_LOGIN.txt, cache/, *.env).
- 키 파일 방식(서비스계정 JSON)도 가능하나 장기 키가 남아 **비권장** — 위 WIF가 안전.
