"""Databricks SQL 한 줄 러너 — 팀원 누구나 같은 방식으로 원천을 조회한다.

사용법:
    python tools/dbx.py q.sql             # 파일의 SQL 실행 → 표로 출력
    python tools/dbx.py q.sql --csv out.csv
    python tools/dbx.py -e "SELECT 1"     # SQL 직접 전달

접속정보(아래 순서로 찾음, 토큰은 절대 커밋 금지):
    1) 환경변수 DATABRICKS_HOST / DATABRICKS_HTTP_PATH / DATABRICKS_TOKEN
    2) .streamlit/secrets.toml 의 [databricks] host / http_path / token

Databricks MCP(execute_sql)가 끊기거나 PENDING 만 돌려줄 때의 대체 경로이기도 하다.
"""
from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

try:  # 회사 프록시(SASE) TLS 통과 — Windows 인증서 저장소 사용
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

from databricks import sql

ROOT = Path(__file__).resolve().parent.parent


def _creds() -> tuple[str, str, str]:
    host = (os.environ.get("DATABRICKS_HOST") or "").strip()
    path = (os.environ.get("DATABRICKS_HTTP_PATH") or "").strip()
    token = (os.environ.get("DATABRICKS_TOKEN") or "").strip()
    if not (host and path and token):
        f = ROOT / ".streamlit" / "secrets.toml"
        if not f.exists():
            sys.exit("접속정보 없음: 환경변수 DATABRICKS_HOST/HTTP_PATH/TOKEN 또는 "
                     ".streamlit/secrets.toml [databricks] 를 설정하세요 (docs: CLAUDE.md)")
        c = tomllib.loads(f.read_text(encoding="utf-8"))["databricks"]
        host, path, token = c["host"].strip(), c["http_path"].strip(), c["token"].strip()
    return host.removeprefix("https://"), path, token


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="SQL 파일 경로")
    ap.add_argument("-e", "--execute", help="SQL 문자열 직접 실행")
    ap.add_argument("--csv", help="결과를 CSV(utf-8-sig, 엑셀 호환)로 저장")
    ap.add_argument("--max-rows", type=int, default=200, help="화면 출력 최대 행수")
    a = ap.parse_args()

    if a.execute:
        q = a.execute
    elif a.file:
        q = Path(a.file).read_text(encoding="utf-8")
    else:
        ap.error("SQL 파일 또는 -e 가 필요합니다")

    host, path, token = _creds()
    # use_cloud_fetch=False 필수: CloudFetch 다운로드 TLS 가 사내 프록시 truststore 와 충돌
    with sql.connect(server_hostname=host, http_path=path, access_token=token,
                     use_cloud_fetch=False) as conn, conn.cursor() as cur:
        cur.execute(q)
        df = cur.fetchall_arrow().to_pandas()

    if a.csv:
        df.to_csv(a.csv, index=False, encoding="utf-8-sig")
        print(f"{len(df):,}행 → {a.csv}")
    with __import__("pandas").option_context("display.width", 250, "display.max_columns", 50):
        print(df.head(a.max_rows).to_string(index=False))
        if len(df) > a.max_rows:
            print(f"... 총 {len(df):,}행 (상위 {a.max_rows}행만 표시)")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # 한글 출력 깨짐 방지
    main()
