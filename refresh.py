r"""주기 갱신 스크립트 — Windows 작업 스케줄러/크론에 등록해서 사용.

  .\.venv\Scripts\python.exe refresh.py              # 판매 증분 갱신(빠름) — 자주(예: 30~60분)
  .\.venv\Scripts\python.exe refresh.py --window=7   # 판매 증분(최근 7일만 재적재) — 시간별용, 더 빠름
  .\.venv\Scripts\python.exe refresh.py --full       # 판매 전체 재적재(느림) — 필요 시
  .\.venv\Scripts\python.exe refresh.py --snapshots  # 재고/상품/고객 스냅샷까지 최신화 — 일 1회 권장
  .\.venv\Scripts\python.exe refresh.py --ips        # 통합 IPS(ips·ips_goods)만 갱신 — 무거워서 별도 일 1회
  (uv run이 .venv를 못 잡는 경우가 있어 .venv 파이썬을 직접 호출. 앱이 켜져 있으면 DuckDB 잠금 충돌하니 앱을 내리고 실행)

판매(MOSS 주문원장)는 누적 증분, 재고/상품/고객은 스냅샷 교체.
DuckDB는 로컬 파일이라 Databricks 쓰기 권한 불필요. 앱 실행 중에도 동작하지만,
DuckDB 파일 잠금이 겹치면 오류가 날 수 있으니 그 경우 잠시 후 재시도하세요.
실행: 프로젝트 폴더에서 `$env:UV_SYSTEM_CERTS="1"` 설정 후 위 명령.
"""
from __future__ import annotations

import sys
import datetime as dt

import store


def main():
    argv = sys.argv[1:]
    args = set(argv)
    # --window=N : 판매/영수증 증분 재적재 윈도우(일). 시간별은 짧게(7), 미지정 시 store 기본(45).
    window = None
    for a in argv:
        if a.startswith("--window="):
            try:
                window = int(a.split("=", 1)[1])
            except ValueError:
                pass
    print(dt.datetime.now().isoformat(timespec="seconds"), "갱신 시작:", " ".join(argv) or "(판매 증분)")
    if "--ips" in args:
        # 통합 IPS만 독립 갱신(무거움) — 판매/스냅샷은 건드리지 않음.
        print("  ips:", store.refresh_named(["ips", "ips_goods"]))
        print("완료(ips):", {k: store.status().get(k) for k in ("ips_refreshed_at", "ips_goods_refreshed_at")})
        return
    full = "--full" in args
    kw = {"window_days": window} if window is not None else {}
    print("  sales:", store.refresh_sales(full=full, **kw))
    if "--snapshots" in args:
        print("  snapshots:", store.refresh_snapshots())
    print("완료:", store.status())


if __name__ == "__main__":
    main()
