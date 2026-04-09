# WorldLLM 시스템 구성도 (Architecture)

> 버전: 2025년 기준 최신 / 논문 작성용 레퍼런스

---

## 1. 시스템 개요

WorldLLM은 LLM(대규모 언어 모델)을 활용하여 창작 세계관(World)을 자동으로 구축하고 시뮬레이션하는 웹 기반 인터랙티브 시스템입니다.
사용자는 세계관 엔트리(인물·세력·사건 등)를 직접 입력하거나 LLM에 위임하여 생성하고,
시뮬레이션 엔진이 매 틱(tick)마다 LLM에 세계 상태를 전달해 이야기를 진행시키며,
그 결과를 타임라인·관계도·통계로 시각화합니다.

---

## 2. 전체 아키텍처 다이어그램

```
┌──────────────────────────────────────────────────────────────────┐
│                        사용자 브라우저 (Client)                     │
│                                                                  │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ 세계관 DB│  │ 시뮬레이션│  │ 타임라인  │  │ 프롬프트 에디터   │  │
│  │  (/)    │  │(/simulat)│  │(/timeline)│  │  (/prompts)      │  │
│  └────┬────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘  │
│       │            │             │                  │            │
│       └────────────┴─────────────┴──────────────────┘            │
│                         REST API (fetch/XHR)                     │
└──────────────────────────────────────────────────────────────────┘
                               │ HTTP/JSON
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                   Flask 웹 서버 (app.py)                           │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │               라우터 & API 엔드포인트                       │    │
│  │  /api/entries  /api/timelines  /api/runs  /api/prompts   │    │
│  │  /api/worlds   /api/logs       /api/stats /api/snapshots │    │
│  └───────────────────────┬──────────────────────────────────┘    │
│                          │                                       │
│  ┌────────────┐  ┌───────┴──────┐  ┌────────────────────────┐   │
│  │ 세션 관리   │  │ 비즈니스 로직 │  │  컨텍스트 프로세서       │   │
│  │(Flask sess)│  │ (app.py 함수) │  │ (current_world inject) │   │
│  └────────────┘  └───────┬──────┘  └────────────────────────┘   │
│                          │                                       │
│         ┌────────────────┼──────────────────┐                    │
│         ▼                ▼                  ▼                    │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐     │
│  │simulation   │  │ llm_client  │  │      models.py       │     │
│  │_engine.py   │  │    .py      │  │  (SQLAlchemy ORM)    │     │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬───────────┘     │
│         │                │                    │                  │
└─────────┼────────────────┼────────────────────┼──────────────────┘
          │                │                    │
          ▼                ▼                    ▼
┌─────────────────┐ ┌─────────────────┐ ┌──────────────┐
│  백그라운드       │ │  GitHub Copilot │ │  SQLite DB   │
│  시뮬레이션 스레드 │ │  LLM API        │ │  (world.db)  │
│  (threading)    │ │  (OpenAI SDK)   │ │              │
└─────────────────┘ └─────────────────┘ └──────────────┘
```

---

## 3. 핵심 구성 요소

### 3.1 프론트엔드 (Templates / Vanilla JS)

| 페이지 | 경로 | 역할 |
|---|---|---|
| 세계 선택 | `/worlds` | 다중 세계관 CRUD 및 선택 |
| 세계관 DB | `/` | 엔트리 조회·생성·수정·삭제, LLM 키워드 자동추출 |
| 시뮬레이션 | `/simulation` | 시뮬레이션 설정·실행·모니터링, 실시간 로그 폴링 |
| 타임라인 | `/timeline` | 에피소드·서사 비트 관리, LLM 타임라인 자동생성 |
| 로그 | `/logs` | 시뮬레이션 이벤트 로그 조회 및 삭제 |
| 관계도 | `/graph` | 엔트리 간 관계 시각화 (Canvas/Force-directed) |
| 통계 | `/stats` | 세계관별 통계 차트 (Chart.js) |
| 프롬프트 에디터 | `/prompts` | LLM 마스터 프롬프트 DB 관리 |
| Export | `/export` | JSON/Markdown 내보내기 |

**공통 UI 컴포넌트 (base.html)**
- Bootstrap 5 다크 테마
- 세계관 선택 표시기 (NavBar)
- 마스터 설정 모달 (RAG 토큰 예산, 최대 글자 수)
- LLM 연결 테스트 모달
- 풀스크린 텍스트 에디터 모달 (`openFse()` / `applyFse()`)

---

### 3.2 백엔드 서버 (app.py)

Flask 기반 단일 서버. 모든 API는 RESTful JSON.

**세션 및 세계관 라우팅**
- `Flask.session["world_id"]` 로 현재 선택된 세계관 추적
- 모든 데이터 API는 `get_world_id()` 를 통해 세계관 범위(scope) 필터링
- `_require_world_redirect()`: 세계관 미선택 시 `/worlds` 로 리다이렉트

**주요 API 그룹**

| 그룹 | 엔드포인트 예시 | 설명 |
|---|---|---|
| 세계관 | `GET/POST /api/worlds` | 세계관 목록·생성 |
| 세계관 선택 | `POST /api/worlds/<id>/select` | 세션에 world_id 저장 |
| 엔트리 | `GET/POST /api/entries` | 세계관 엔트리 CRUD |
| 타임라인 | `GET/POST /api/timelines` | 타임라인 CRUD |
| 에피소드 | `GET/POST /api/episodes` | 에피소드 CRUD |
| 서사 비트 | `GET/POST /api/story-beats` | 스토리 비트 CRUD |
| 시뮬레이션 | `POST /api/runs/start` | 시뮬레이션 시작 (백그라운드) |
| 스냅샷 | `GET/POST /api/snapshots` | 세계 상태 저장·복원 |
| 로그 | `GET /api/logs`, `DELETE /api/logs` | 로그 조회·삭제 |
| 통계 | `GET /api/stats` | 세계관별 통계 |
| 프롬프트 | `GET/PUT /api/prompts/<key>` | LLM 프롬프트 편집 |
| LLM 타임라인 | `POST /api/timelines/generate-llm` | LLM 타임라인 자동생성 |

---

### 3.3 시뮬레이션 엔진 (simulation_engine.py)

백그라운드 스레드(`threading.Thread`)에서 비동기 실행.

```
SimulationRun 생성
      │
      ▼
 프롬프트 오버라이드 로드 (LlmPromptConfig DB)
      │
      ▼
 세계관 엔트리 조회 (RAG 필터링 적용 시)
      │
      ▼
 ┌─── 틱(Tick) 루프 ───────────────────────────────────────┐
 │                                                         │
 │  world_state 직렬화 → LLM 전송 (llm_client.run_tick)    │
 │       │                                                 │
 │       ▼                                                 │
 │  LLM 응답 파싱 (JSON)                                    │
 │   ├─ world_event → SimulationLog                        │
 │   ├─ new_entries → WorldEntry (world_id 포함)            │
 │   ├─ updated_entries → WorldEntry 업데이트               │
 │   └─ deactivated_entries → WorldEntry 비활성화           │
 │       │                                                 │
 │  자동 요약 조건 충족 시 → llm_client.run_summary()        │
 │       │                                                 │
 │  SimulationLog 저장 → DB 커밋                            │
 └─────────────────────────────────────────────────────────┘
      │
      ▼
 SimulationRun 종료 상태 업데이트
```

**RAG(Retrieval-Augmented Generation)**
- 엔트리 총 토큰 수가 `rag_token_budget`의 50% 초과 시 활성화
- 각 엔트리의 핵심 키워드 5개로 현재 컨텍스트와 코사인 유사도 계산
- 필수 유저 엔트리 + 상위 관련 LLM 엔트리만 선택하여 전송

---

### 3.4 LLM 클라이언트 (llm_client.py)

GitHub Copilot API (OpenAI SDK 호환)를 통한 LLM 연동.

```python
client = OpenAI(
    base_url="https://api.githubcopilot.com",
    api_key=GITHUB_TOKEN,
    default_headers={
        "Editor-Version": "vscode/...",
        "Copilot-Integration-Id": "vscode-chat",
    }
)
```

**3가지 LLM 호출 유형**

| 함수 | 용도 | 프롬프트 키 |
|---|---|---|
| `run_tick()` | 시뮬레이션 1틱 진행 | `simulation_system`, `simulation_user` |
| `run_summary()` | 이전 틱 요약 (컨텍스트 압축) | `summary` |
| `generate_timeline()` | 타임라인 에피소드 자동생성 | `timeline_generate` |

모든 함수는 `prompt_overrides: dict` 파라미터를 받아 DB에 저장된 프롬프트를 코드 수정 없이 교체 가능.

---

### 3.5 데이터 모델 (models.py / SQLite)

```
World (세계관)
  │
  ├── WorldEntry (엔트리: 인물/세력/사건/관념/물건/종족)
  │     └── keywords: JSON 배열 (RAG용 핵심 키워드)
  │
  ├── Timeline (타임라인)
  │     ├── Episode (에피소드)
  │     │     └── EpisodeEntry (에피소드-엔트리 연결)
  │     ├── StoryBeat (서사 비트)
  │     └── NarrativeArc (서사 아크)
  │
  ├── SimulationConfig (시뮬레이션 설정)
  │
  ├── SimulationRun (실행 기록)
  │     └── SimulationLog (이벤트 로그)
  │
  └── WorldSnapshot (세계 상태 스냅샷)

AppSettings (글로벌 설정 - 세계관 무관)
  └── max_llm_entry_chars, max_user_entry_chars, rag_token_budget

LlmPromptConfig (LLM 프롬프트 템플릿 - 세계관 무관)
  └── simulation_system, simulation_user, summary, timeline_generate
```

**주요 설계 결정**
- 모든 세계관 관련 테이블에 `world_id FK` 부여 → 완전한 데이터 격리
- `nullable=True` 로 선언하여 기존 데이터와 하위호환성 유지
- 타임스탬프는 모두 UTC로 저장 (`datetime.utcnow()`), ISO 8601 + `'Z'` suffix로 직렬화 → JS `new Date()` 자동 로컬 시간 변환

---

## 4. 데이터 흐름 (Data Flow)

### 4.1 시뮬레이션 실행 흐름

```
사용자 → [시작 버튼] → POST /api/runs/start
                              │
                              ▼
                    SimulationRun DB 생성
                              │
                    threading.Thread 시작
                              │
                    ┌─── 틱 루프 ────────────────────────────┐
                    │  1. 활성 엔트리 조회 (RAG 필터)         │
                    │  2. world_state JSON 구성              │
                    │  3. GitHub Copilot API 호출            │
                    │  4. JSON 응답 파싱 & DB 반영            │
                    │  5. SimulationLog 기록                 │
                    │  6. N틱마다 자동 요약 (run_summary)     │
                    └────────────────────────────────────────┘
                              │
사용자 ← [폴링 GET /api/runs/<id>/logs] ← 실시간 로그 조회
```

### 4.2 LLM 타임라인 생성 흐름

```
사용자 → 엔트리 선택 + 추가 프롬프트 입력
       → POST /api/timelines/generate-llm
              │
              ▼
       선택 엔트리 = 메인 참조
       나머지 엔트리 = 컨텍스트 참조
              │
              ▼
       LlmPromptConfig["timeline_generate"] 로드
              │
              ▼
       GitHub Copilot API → JSON 에피소드 목록 반환
              │
              ▼
       Timeline + Episode DB 저장
              │
사용자 ← 타임라인 자동 표시
```

### 4.3 프롬프트 편집 흐름

```
사용자 → /prompts 페이지
       → 텍스트 편집 (풀스크린 에디터 지원)
       → PUT /api/prompts/<key>
              │
              ▼
       LlmPromptConfig.content 업데이트
              │
       다음 시뮬레이션/타임라인 생성 시 즉시 반영
       (코드 수정·재시작 불필요)
```

---

## 5. 기술 스택

| 계층 | 기술 |
|---|---|
| 프론트엔드 | HTML5, Bootstrap 5, Vanilla JavaScript, Chart.js |
| 백엔드 | Python 3.x, Flask 3.x |
| ORM | SQLAlchemy 2.x |
| DB | SQLite (단일 파일 `world.db`) |
| LLM API | GitHub Copilot API (OpenAI SDK 호환) |
| 모델 | gpt-4o (GitHub Copilot 제공) |
| 배포 | Gunicorn (운영), Flask dev server (개발) |
| 환경변수 | python-dotenv (`.env` 파일) |

---

## 6. 디렉토리 구조

```
llm_test/
├── app.py                  # Flask 서버, 라우터, API 엔드포인트
├── models.py               # SQLAlchemy 모델 정의
├── llm_client.py           # LLM API 클라이언트 (GitHub Copilot)
├── simulation_engine.py    # 시뮬레이션 엔진 (백그라운드 스레드)
├── run.py                  # 서버 시작 진입점
├── requirements.txt        # Python 패키지 목록
├── .env                    # 환경변수 (GITHUB_TOKEN 등, git 제외)
├── world.db                # SQLite 데이터베이스 (자동 생성)
├── SETUP.md                # 설치·실행 가이드
├── ARCHITECTURE.md         # 시스템 구성도 (이 파일)
└── templates/
    ├── base.html           # 공통 레이아웃 (NavBar, 모달, FSE)
    ├── worlds.html         # 세계관 선택·관리
    ├── index.html          # 세계관 DB (엔트리 관리)
    ├── simulation.html     # 시뮬레이션 실행·모니터링
    ├── timeline.html       # 타임라인·에피소드·서사 관리
    ├── logs.html           # 시뮬레이션 로그
    ├── graph.html          # 관계도 시각화
    ├── stats.html          # 통계 차트
    ├── prompts.html        # LLM 프롬프트 에디터
    └── export.html         # JSON/Markdown 내보내기
```

---

## 7. 보안 및 확장성 고려사항

- **인증**: 현재 단일 사용자 로컬 환경 가정. 다중 사용자 환경 시 Flask-Login 또는 세션 암호화 추가 권장.
- **DB 확장**: SQLite → PostgreSQL 전환 시 SQLAlchemy 커넥션 문자열만 변경.
- **LLM 교체**: `llm_client.py`의 `client` 초기화 부분만 수정하면 OpenAI, Anthropic 등 다른 API로 교체 가능 (OpenAI SDK 호환 API).
- **프롬프트 버전관리**: `LlmPromptConfig`에 `updated_at` 기록됨. 추후 히스토리 테이블 추가로 버전 관리 가능.
- **동시 시뮬레이션**: 현재 단일 스레드 시뮬레이션. 다중 세계관 동시 실행 시 스레드 풀(ThreadPoolExecutor) 또는 Celery 작업 큐 도입 권장.
