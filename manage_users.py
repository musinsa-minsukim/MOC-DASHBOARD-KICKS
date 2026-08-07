r"""대시보드 로그인 계정 관리 (로컬 실행).

비밀번호는 입력 시 화면에 보이지 않으며, 해시(bcrypt)로만 auth_config.yaml에 저장됩니다.
평문 비밀번호는 어디에도 저장되지 않습니다.

  .\.venv\Scripts\python.exe manage_users.py list
  .\.venv\Scripts\python.exe manage_users.py add <아이디>
  .\.venv\Scripts\python.exe manage_users.py remove <아이디>

(uv run이 .venv 대신 다른 임시환경을 잡아 ModuleNotFoundError가 날 수 있어 .venv 파이썬을 직접 호출.
 프로젝트 폴더에서 실행.)
"""
from __future__ import annotations

import os
import sys
import getpass
import secrets

import yaml
from streamlit_authenticator.utilities.hasher import Hasher

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_config.yaml")


def _load() -> dict:
    if os.path.exists(CFG):
        with open(CFG, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {"cookie": {"name": "musinsa_dash_auth", "key": secrets.token_hex(32), "expiry_days": 30},
            "credentials": {"usernames": {}}}


def _save(cfg: dict):
    with open(CFG, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    cfg = _load()
    users = cfg.setdefault("credentials", {}).setdefault("usernames", {})

    if cmd == "list":
        if not users:
            print("(등록된 계정 없음)")
        for u, d in users.items():
            print(f"- {u}  ({d.get('name', '')})")

    elif cmd == "add" and len(sys.argv) >= 3:
        u = sys.argv[2].strip()
        cur = users.get(u, {}).get("name") or u          # 기존 계정이면 현재 이름이 기본값
        name = input(f"이름(표시용) [{cur}] (Enter=유지): ").strip() or cur
        pw = getpass.getpass("비밀번호: ")
        pw2 = getpass.getpass("비밀번호 확인: ")
        if pw != pw2:
            print("비밀번호가 일치하지 않습니다.")
            return
        if len(pw) < 6:
            print("비밀번호는 6자 이상이어야 합니다.")
            return
        users[u] = {"name": name, "email": f"{u}@musinsa.com", "password": Hasher.hash(pw)}
        _save(cfg)
        print(f"계정 '{u}' 저장 완료 (해시로 저장됨).")

    elif cmd == "remove" and len(sys.argv) >= 3:
        u = sys.argv[2].strip()
        if users.pop(u, None) is not None:
            _save(cfg)
            print(f"계정 '{u}' 삭제됨.")
        else:
            print(f"'{u}' 계정이 없습니다.")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
