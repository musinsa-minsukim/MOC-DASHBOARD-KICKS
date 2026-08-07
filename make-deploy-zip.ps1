# 무신사 대시보드 배포 zip 생성기
# - 소스 코드만 담음(node_modules/.venv/cache/dist/시크릿/로그 제외)
# - 리눅스(Cloud Shell) unzip 호환: 엔트리 경로를 정방향 슬래시(/)로 기록
# - 기존 zip은 FileMode.Create로 덮어씀(Remove-Item 불필요)
# 사용: powershell -ExecutionPolicy Bypass -File make-deploy-zip.ps1
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null

$src = Split-Path -Parent $MyInvocation.MyCommand.Path   # 이 스크립트가 있는 프로젝트 폴더
$zip = Join-Path (Split-Path -Parent $src) "mok-dashboard-deploy.zip"   # 상위 폴더에 생성(소스 트리 밖)

$bs = [char]92; $fw = [char]47   # \ , /
$excluded = {
  param($r)
  if ($r -like "web/node_modules/*") { return $true }
  if ($r -like "web/dist/*")         { return $true }
  if ($r -like "cache/*")            { return $true }
  if ($r -like ".venv/*")            { return $true }
  if ($r -like ".git/*")             { return $true }
  if ($r -like ".streamlit/*")       { return $true }   # secrets.toml 등 로컬 시크릿
  if ($r -like "*__pycache__/*")     { return $true }
  if ($r -like "*.pyc")              { return $true }
  if ($r -like "*.log")              { return $true }
  if ($r -like "*.zip")              { return $true }
  if ($r -eq  "auth_config.yaml")    { return $true }   # bcrypt 해시·쿠키키(시크릿) — GCP Secret Manager로 주입
  if ($r -eq  "INITIAL_LOGIN.txt")   { return $true }
  return $false
}

$fs = [IO.File]::Open($zip, [IO.FileMode]::Create)
$arch = New-Object IO.Compression.ZipArchive($fs, [IO.Compression.ZipArchiveMode]::Create)
$count = 0
Get-ChildItem -Path $src -Recurse -File -Force | ForEach-Object {
  $rel = $_.FullName.Substring($src.Length + 1).Replace($bs, $fw)
  if (-not (& $excluded $rel)) {
    $entry = $arch.CreateEntry($rel, [IO.Compression.CompressionLevel]::Optimal)
    $es = $entry.Open()
    $fsIn = [IO.File]::OpenRead($_.FullName)
    $fsIn.CopyTo($es); $fsIn.Dispose(); $es.Dispose()
    $count++
  }
}
$arch.Dispose(); $fs.Dispose()
Write-Host ("ZIP 생성: {0}" -f $zip)
Write-Host ("파일 수: {0}  크기: {1} MB" -f $count, [math]::Round((Get-Item $zip).Length/1MB, 2))
Write-Host "다음: Cloud Shell ⋮ Upload → rm -rf ~/musinsa-dashboard → unzip -o ~/mok-dashboard-deploy.zip -d ~/musinsa-dashboard → cd → gcloud run deploy --source . --region=asia-northeast3"
