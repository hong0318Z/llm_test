"""
실행 진입점
사용법:
  pip install -r requirements.txt
  cp .env.example .env  # 편집 후
  python run.py
"""
from dotenv import load_dotenv
load_dotenv()

from app import app

if __name__ == "__main__":
    print("\n WorldLLM 시스템 시작")
    print(" http://localhost:5000 에서 접속하세요\n")
    app.run(debug=True, host="0.0.0.0", port=5000)
