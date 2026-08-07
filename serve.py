"""Cloud Run 엔트리포인트.
스크립트 자신의 위치(/app)를 sys.path에 직접 추가해 `api` 모듈 import를 보장한다.
(컨테이너 런타임의 cwd/PYTHONPATH 차이와 무관하게 동작)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from api import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
