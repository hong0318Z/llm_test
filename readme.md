# WorldLLM 설치 및 실행 가이드

AI 기반 세계관 시뮬레이션 시스템의 초기 설정 가이드입니다.

---

## 사전 요구사항

| 항목 | 최소 버전 | 확인 명령 |
|------|-----------|-----------|
| Python | 3.10 이상 | `python3 --version` |
| Git | - | `git --version` |
| GitHub 계정 | Copilot 구독 필요 | - |

---

## 1단계 — 코드 받기

```bash
git clone https://github.com/hong0318z/llm_test.git
cd llm_test
git checkout claude/worldbuilding-llm-system-fQCoJ
```

---

## 2단계 — 가상환경 생성 및 패키지 설치

```bash
# 가상환경 생성
python3 -m venv venv

# 활성화
# macOS / Linux:
source venv/bin/activate

# Windows (PowerShell):
# venv\Scripts\Activate.ps1

# 패키지 설치
pip install -r requirements.txt
```

설치되는 주요 패키지:
- `flask` — 웹 프레임워크
- `flask-sqlalchemy` — DB ORM
- `openai` — GitHub Copilot API 클라이언트
- `python-dotenv` — 환경변수 로드

---

## 3단계 — GitHub Token 발급

GitHub Copilot API를 사용하려면 Personal Access Token(PAT)이 필요합니다.

1. GitHub 로그인 → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
2. **Generate new token (classic)** 클릭
3. Note: `WorldLLM` (자유롭게)
4. Expiration: 원하는 기간 설정
5. 권한 선택: `read:user` 또는 `copilot` 체크
6. **Generate token** → 표시된 토큰 복사 (다시 볼 수 없음!)

> **Copilot 구독 확인**: GitHub Settings → Copilot에서 구독 상태 확인 필요.
> 구독이 없으면 API 호출 시 401 오류 발생.

---

## 4단계 — 환경변수 설정 (.env 파일)

프로젝트 루트에 `.env` 파일을 생성합니다.

```bash
# macOS / Linux
cp .env.example .env   # 예제 파일이 있는 경우
# 또는 직접 생성:
touch .env
```

`.env` 파일 내용:

```env
# ─────────────────────────────────────────
# 필수 설정
# ─────────────────────────────────────────

# GitHub Personal Access Token (Copilot 구독 계정)
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ─────────────────────────────────────────
# 선택 설정
# ─────────────────────────────────────────

# 사용할 LLM 모델 (기본값: claude-sonnet-4.5)
# LLM_MODEL=claude-sonnet-4.5

# NovelAI API 키 (이미지 생성 기능 사용 시 필요)
# https://novelai.net/ 에서 API 키 발급
# NOVELAI_API_KEY=pst-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 서버 포트 (Docker 기본값: 4318)
# PORT=4318

# Flask 시크릿 키 (세션 암호화용, 배포 시 반드시 변경)
# SECRET_KEY=your-very-secret-key-change-this
```

> **보안 주의**: `.env` 파일은 절대 git에 커밋하지 마세요.
> `.gitignore`에 `.env`가 포함되어 있는지 확인하세요.

---

## 5단계 — 앱 실행

```bash
# 가상환경이 활성화된 상태에서
python run.py
```

정상 실행 시 출력:
```
 WorldLLM 시스템 시작
 http://localhost:5001 에서 접속하세요
```

브라우저에서 `http://localhost:5001` 접속.

---

## Synology Docker 배포 및 로컬 LLM

Synology Container Manager에서 프로젝트 폴더를 Git으로 클론/업데이트한 뒤, 루트의 `docker-compose.yml`을 Compose 프로젝트로 실행하세요. 외부 포트는 **4318**이며 데이터베이스는 `./data/worldbuilding.db`, 이미지는 `./storage`에 보존됩니다.

```bash
docker compose up -d --build
```

접속 주소는 `http://NAS_IP:4318`입니다. 업데이트할 때는 `git pull` 뒤에 위 명령을 다시 실행하면 됩니다. `.env`와 `data/`는 Git에 올리지 마세요.

Ollama/LM Studio/vLLM처럼 OpenAI 호환 API를 쓰려면 `.env`에 다음을 지정합니다. NAS Docker에서 호스트의 Ollama를 호출할 수 없으면 `host.docker.internal` 대신 Ollama가 설치된 장비의 LAN IP를 사용하세요.

```env
LLM_BASE_URL=http://192.168.0.10:11434/v1
LLM_API_KEY=local-no-key
LLM_MODEL=qwen3:8b
EMBEDDING_BASE_URL=http://192.168.0.10:11434/v1
EMBEDDING_MODEL=nomic-embed-text
SECRET_KEY=긴_임의_문자열
```

웹의 **마스터 설정**에서 `임베딩 사용`을 켜고 모델명(예: `nomic-embed-text`) 및 틱당 참조 수를 설정할 수 있습니다. 임베딩은 기본적으로 꺼져 있어, 활성화하지 않으면 기존 키워드 RAG만 사용합니다. 이미 저장된 엔트리는 상세 화면의 임베딩 생성 API 또는 향후 수정 시 갱신됩니다.

---

## 6단계 — 첫 실행 (세계관 생성)

1. 접속하면 **세계관 선택** 화면이 표시됩니다
2. **새 세계관 만들기** 카드 클릭
3. 세계관 이름과 설명 입력 후 저장
4. 자동으로 메인 화면으로 이동됩니다

> 기존 데이터가 있는 경우 자동으로 **"기본 세계관"** 이 생성되고 기존 데이터가 그 안에 마이그레이션됩니다.

---

## LLM 연결 테스트

앱 상단 우측의 **"LLM 연결 테스트"** 버튼으로 API 연결을 확인할 수 있습니다.

- ✅ 연결 성공: 토큰과 모델명이 표시됨
- ❌ 연결 실패: `GITHUB_TOKEN` 값과 Copilot 구독 상태 재확인

---

## 사용 가능한 LLM 모델

| 모델 ID | 설명 |
|---------|------|
| `claude-sonnet-4.5` | 기본값, 균형 잡힌 성능 |
| `claude-sonnet-4` | 이전 버전 |
| `gpt-4o` | OpenAI GPT-4o |
| `o3-mini` | 추론 특화 모델 |

`.env`의 `LLM_MODEL` 값을 변경해 모델을 교체할 수 있습니다.

---

## 데이터 위치

| 항목 | 경로 |
|------|------|
| SQLite DB | `instance/worldbuilding.db` |
| 업로드 이미지 | `storage/` |
| 환경변수 | `.env` |

---

## 자주 발생하는 오류

### `GITHUB_TOKEN 환경변수가 필요합니다`
→ `.env` 파일이 없거나 `GITHUB_TOKEN`이 설정되지 않음

### `401 Unauthorized`
→ Token이 만료되었거나 Copilot 구독이 없음

### `Port already in use`
```bash
# 포트 변경: .env에 PORT=5002 추가
PORT=5002
```

### `ModuleNotFoundError`
→ 가상환경이 활성화되지 않았거나 `pip install -r requirements.txt` 미실행

---

## 배포 시 추가 설정

```env
# 프로덕션 배포 시 .env에 추가
SECRET_KEY=랜덤하고-긴-문자열-반드시-변경할것
```

Flask를 직접 실행하는 대신 **gunicorn** 사용을 권장합니다:

```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5001 "app:app"
```

> **주의**: 시뮬레이션 엔진은 백그라운드 스레드를 사용합니다.
> gunicorn worker 수는 1~2개를 권장합니다 (`-w 2`).

---

## 디렉토리 구조

```
llm_test/
├── app.py              # Flask 앱, API 라우트
├── models.py           # SQLAlchemy 모델 (World, WorldEntry, ...)
├── llm_client.py       # GitHub Copilot API 클라이언트
├── simulation_engine.py # 시뮬레이션 틱 엔진
├── run.py              # 실행 진입점
├── requirements.txt    # Python 패키지 목록
├── .env                # 환경변수 (직접 생성, git 제외)
├── instance/           # SQLite DB 자동 생성
├── storage/            # 업로드 이미지
└── templates/          # HTML 템플릿
    ├── worlds.html     # 세계관 선택 페이지
    ├── base.html       # 공통 레이아웃
    ├── index.html      # 세계관 DB (엔트리 관리)
    ├── simulation.html # 시뮬레이션 실행
    ├── timeline.html   # 타임라인 관리
    ├── logs.html       # 시뮬레이션 로그
    ├── graph.html      # 관계도
    └── stats.html      # 통계
```
