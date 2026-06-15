"""
실행 진입점
사용법:
  pip install -r requirements.txt
  cp .env.example .env  # 편집 후
  python run.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print("\n WorldLLM 시스템 시작")
    print(f" http://localhost:{port} 에서 접속하세요\n")
    app.run(debug=True, host="0.0.0.0", port=port)
