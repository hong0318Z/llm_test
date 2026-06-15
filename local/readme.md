# 로컬 oMLX 테스트용 사본

이 폴더는 oMLX(로컬 OpenAI 호환 서버)와 연동해서 기능을 테스트하기 위한
독립 실행용 앱 사본입니다. 원본 프로젝트(상위 폴더)와 별개로 동작하며,
DB(`worldbuilding.db`)도 이 폴더 안에 따로 생성됩니다.

## 사전 준비

1. oMLX를 실행해서 `http://127.0.0.1:8000/v1` 에서 OpenAI 호환 API가
   떠 있고, 모델 목록에 `mlx-community--gemma-4-26b-a4b-it-8bit` 가
   "준비됨" 상태인지 확인하세요.
2. `.env` 파일을 열어 필요하면 값을 수정하세요.
   - `LLM_BASE_URL`: oMLX의 OpenAI 호환 엔드포인트
   - `LLM_MODEL`: 사용할 모델명 (oMLX 모델 목록의 이름과 동일해야 함)
   - `LLM_API_KEY`: oMLX가 키를 검증하지 않으면 임의 문자열로 둬도 됩니다.

## 실행

```bash
cd local
pip install -r requirements.txt
python run.py
```

기본 포트는 5002 입니다 (`http://localhost:5002`). 원본 앱(5001)과
동시에 띄워도 충돌하지 않습니다.

## 모델 선택

앱 접속 후 설정 화면에서 "세계관 시뮬레이션용" / "NAI 프롬프트 생성용"
모델명을 `.env`의 `LLM_MODEL`과 동일한 값(`mlx-community--gemma-4-26b-a4b-it-8bit`)으로
지정해야 oMLX 모델이 사용됩니다.
