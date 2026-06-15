# WorldLLM 시스템 아키텍처

> 본 문서는 WorldLLM의 전체 구조, 데이터 흐름, 구성 요소 간 관계를 도식화한다.
> GitHub에서 Mermaid 다이어그램이 자동 렌더링된다.

---

## 1. 전체 시스템 구성도

```mermaid
graph TB
    subgraph Browser["🌐 브라우저 (Web UI)"]
        UI_DB["세계관 DB\nindex.html"]
        UI_SIM["시뮬레이션\nsimulation.html"]
        UI_GRAPH["관계도\ngraph.html"]
        UI_LOG["로그\nlogs.html"]
        UI_EXPORT["내보내기\nexport.html"]
        UI_BASE["공통 네비게이션\nbase.html\n(마스터 설정 / LLM 테스트)"]
    end

    subgraph Flask["⚙️ Flask 서버 (app.py)"]
        API_ENTRY["엔트리 API\n/api/entries/*"]
        API_CONFIG["설정 API\n/api/configs/*"]
        API_RUN["실행 API\n/api/runs/*"]
        API_SETTINGS["마스터 설정 API\n/api/settings"]
        API_MISC["유틸 API\n/api/backup\n/api/restore\n/api/translate\n/api/generate-entry\n/api/token-estimate"]
    end

    subgraph Engine["🔄 시뮬레이션 엔진 (simulation_engine.py)"]
        TICK_LOOP["틱 루프\nrun_simulation()"]
        ENTRY_GUARD["유저 엔트리 보호\n_apply_tick_result()"]
        AUTO_SUMMARY["자동 요약 트리거\n_run_auto_summary()"]
    end

    subgraph LLM["🤖 LLM 클라이언트 (llm_client.py)"]
        SERIALIZER["세계관 직렬화\nserialize_world_state()"]
        RUN_TICK["틱 실행\nrun_tick()"]
        RUN_SUMMARY["요약 실행\nrun_summary()"]
        GEN_ENTRY["엔트리 생성\ngenerate_entry()"]
        TRANSLATE["번역\ntranslate_entries()"]
    end

    subgraph DB["🗄️ SQLite (worldbuilding.db)"]
        T_ENTRY["world_entries"]
        T_CONFIG["simulation_configs"]
        T_RUN["simulation_runs"]
        T_LOG["simulation_logs"]
        T_SNAP["world_snapshots"]
        T_SETTINGS["app_settings"]
    end

    subgraph External["☁️ 외부 API"]
        COPILOT["GitHub Copilot API\nhttps://api.githubcopilot.com\nmodel: claude-sonnet-4.5"]
    end

    Browser <-->|HTTP REST / JSON| Flask
    Flask <-->|SQLAlchemy ORM| DB
    Flask -->|백그라운드 Thread| Engine
    Engine <-->|SQLAlchemy| DB
    Engine -->|함수 호출| LLM
    LLM <-->|OpenAI SDK| External
```

---

## 2. 틱 실행 흐름 (Sequence Diagram)

```mermaid
sequenceDiagram
    actor User as 👤 사용자
    participant UI as simulation.html
    participant API as app.py
    participant Thread as Background Thread
    participant Engine as simulation_engine.py
    participant LLM as llm_client.py
    participant DB as SQLite
    participant Copilot as GitHub Copilot API

    User->>UI: 시뮬레이션 시작 클릭
    UI->>API: POST /api/runs {config_id, entry_ids, ...}
    API->>DB: SimulationRun 생성 (status=pending)
    API->>Thread: threading.Thread(run_simulation).start()
    API-->>UI: 202 Accepted {run_id}

    loop 폴링 (2초마다)
        UI->>API: GET /api/runs/{id}
        API-->>UI: {status, current_tick, tokens...}
    end

    Thread->>Engine: run_simulation(run_id)
    Engine->>DB: status = "running"

    loop 각 틱 (1 ~ total_ticks)
        Engine->>DB: status 재조회 (취소 감지)
        Note over Engine: cancelled이면 루프 탈출

        Engine->>DB: 활성 엔트리 쿼리
        Engine->>LLM: needs_summary(entries)
        LLM-->>Engine: 토큰 수 >= 120,000?

        alt 컨텍스트 한계 근접
            Engine->>LLM: run_summary(config, entries)
            LLM->>Copilot: POST /chat/completions (temp=0.3)
            Copilot-->>LLM: 요약 JSON
            LLM-->>Engine: summaries[]
            Engine->>DB: 요약 엔트리 저장, 원본 is_summarized=true
        end

        Engine->>LLM: run_tick(config, tick, entries)
        LLM->>LLM: serialize_world_state() ← [🔒유저] 태그 부여
        LLM->>Copilot: POST /chat/completions (temp=0.8)
        Copilot-->>LLM: 틱 결과 JSON
        LLM-->>Engine: {events, entry_updates, new_entries, deactivated_entries}

        Engine->>Engine: _apply_tick_result()
        Note over Engine: 유저 엔트리 수정/비활성화 차단<br/>→ 파생 엔트리 신규 생성으로 대체
        Engine->>DB: 엔트리 업데이트 / 신규 생성 / 로그 기록
    end

    Engine->>DB: status = "done", ended_at = now
    UI->>UI: 폴링에서 done 감지 → 완료 표시
```

---

## 3. 데이터 모델 (ER Diagram)

```mermaid
erDiagram
    WorldEntry {
        int id PK
        string title
        string category "세력/인물/관념/물건/종족/사건"
        text content
        text references_json "참조 엔트리 ID 배열"
        string created_by "user / llm"
        int tick_created
        bool is_active
        bool is_summarized
        datetime created_at
        datetime updated_at
    }

    SimulationConfig {
        int id PK
        string name
        text prompt_level_1 "세계관 기반 법칙"
        text prompt_level_2 "시대/맥락/현재 상황"
        text prompt_level_3 "틱 진행 규칙"
        int tick_count
        bool is_active
        datetime created_at
    }

    SimulationRun {
        int id PK
        int config_id FK
        string status "pending/running/done/error/cancelled"
        int current_tick
        int total_ticks
        int total_tokens_in
        int total_tokens_out
        int total_tokens
        text selected_entry_ids_json
        bool exclude_llm_entries
        datetime started_at
        datetime ended_at
    }

    SimulationLog {
        int id PK
        int run_id FK
        int tick_number
        string event_type "world_event/entry_updated/entry_created/entry_deactivated/entry_protected/context_summary/error"
        text description
        text affected_entries_json
        text llm_reasoning
        text raw_llm_output
        int tokens_in
        int tokens_out
        int tokens_total
        datetime created_at
    }

    WorldSnapshot {
        int id PK
        string name
        text description
        text entries_json "전체 엔트리 JSON 스냅샷"
        int entry_count
        datetime created_at
    }

    AppSettings {
        int id PK "항상 1 (싱글톤)"
        int max_llm_entry_chars "LLM 생성 엔트리 최대 글자 수"
        int max_user_entry_chars "유저 입력 UI 가이드 한도"
    }

    SimulationConfig ||--o{ SimulationRun : "실행"
    SimulationRun ||--o{ SimulationLog : "로그"
```

---

## 4. 유저 엔트리 보호 흐름

```mermaid
flowchart TD
    LLM_OUT["LLM 응답\n{entry_updates, deactivated_entries}"]

    LLM_OUT --> CHECK_UPDATE["entry_updates 처리"]
    CHECK_UPDATE --> IS_USER_U{created_by\n== 'user'?}

    IS_USER_U -->|Yes 🔒| CREATE_DERIVED["파생 엔트리 생성\n제목: '원본명 (변화 - 틱 N)'\ncreated_by = llm\nreferences = [원본 ID]"]
    IS_USER_U -->|No| UPDATE_ENTRY["엔트리 내용 업데이트\n(LLM 생성 엔트리만 가능)"]

    LLM_OUT --> CHECK_DEACT["deactivated_entries 처리"]
    CHECK_DEACT --> IS_USER_D{created_by\n== 'user'?}

    IS_USER_D -->|Yes 🔒| LOG_PROTECTED["차단 로그 기록\nevent_type = entry_protected\n엔트리 상태 변경 없음"]
    IS_USER_D -->|No| DEACTIVATE["is_active = False"]

    style IS_USER_U fill:#7c4dbb,color:#fff
    style IS_USER_D fill:#7c4dbb,color:#fff
    style CREATE_DERIVED fill:#2a8a6e,color:#fff
    style LOG_PROTECTED fill:#b86a1e,color:#fff
```

---

## 5. 컨텍스트 관리 전략

```mermaid
flowchart LR
    ENTRIES["활성 엔트리\n(is_active=true\nis_summarized=false)"]

    ENTRIES --> SERIAL["serialize_world_state()\n엔트리당 max_llm_entry_chars 글자로 잘림\n[🔒유저] 태그 부여"]
    SERIAL --> TOKEN_EST["토큰 추정\nlen(text) / 1.5"]

    TOKEN_EST --> THRESHOLD{≥ 120,000\n토큰?}

    THRESHOLD -->|Yes| SUMMARY["run_summary()\ntemp=0.3 / 카테고리별 압축\n→ 요약 엔트리 신규 저장\n→ 원본 is_summarized=true"]
    THRESHOLD -->|No| TICK["run_tick()\ntemp=0.8 / 세계관 진화"]

    SUMMARY --> TICK

    style THRESHOLD fill:#c0392b,color:#fff
    style SUMMARY fill:#3b6fd4,color:#fff
    style TICK fill:#2a8a6e,color:#fff
```

---

## 6. 시스템 동작 안내

### 기본 사용 흐름

```
1. 세계관 DB  →  엔트리 입력 (세력 / 인물 / 관념 / 물건 / 종족 / 사건)
                  └─ LLM 생성 버튼으로 내용 자동 작성 가능

2. 시뮬레이션  →  설정 구성 (3-레벨 프롬프트 + 틱 수)
                  └─ 마스터 설정에서 LLM/유저 엔트리 글자 한도 설정

3. 시뮬레이션 시작  →  틱마다 LLM이 세계관 자율 진화
                       └─ 유저 엔트리는 보호됨 (수정/비활성화 불가)

4. 결과 확인  →  로그 / 관계도 / Export (JSON + CSV)
```

### 핵심 설계 원칙

| 원칙 | 구현 방식 |
|------|---------|
| **유저 세계관 코어 보호** | `[🔒유저]` 태그 + 코드 레벨 차단 |
| **LLM 생성과 유저 입력 구분** | `created_by: "user" / "llm"` 필드 |
| **컨텍스트 자동 관리** | 120k 토큰 초과 시 요약 압축 자동 실행 |
| **재현성 확보** | 모든 틱의 reasoning, raw 응답, 토큰 수 DB 저장 |
| **앱 재시작 안전성** | 시작 시 orphan 런 자동 cancelled 처리 |

### 주요 설정값

| 설정 | 위치 | 기본값 |
|------|------|--------|
| LLM 생성 엔트리 최대 글자 | 마스터 설정 (UI) | 500자 |
| 유저 입력 가이드 한도 | 마스터 설정 (UI) | 1,000자 |
| 자동 요약 임계값 | `llm_client.py` `CONTEXT_SUMMARY_THRESHOLD` | 120,000 토큰 |
| LLM 최대 출력 토큰 | `llm_client.py` `MAX_TOKENS` | 16,000 |
| 틱 창의성 (temperature) | `llm_client.py` `run_tick()` | 0.8 |

---

## 7. 로컬 LLM 전환 로드맵

현재 시스템은 GitHub Copilot API (claude-sonnet-4.5)를 사용하지만,
**OpenAI SDK 호환 인터페이스**로 추상화되어 있어 최소한의 수정으로 로컬 LLM으로 전환 가능하다.

### 현재 구조 (API)

```
llm_client.py
    └─ get_llm_client()
          └─ OpenAI(base_url="https://api.githubcopilot.com", api_key=GITHUB_TOKEN)
```

### 로컬 LLM 전환 시 변경 지점

`llm_client.py`의 `get_llm_client()` 함수 한 곳만 수정하면 된다.

```python
# 현재 (GitHub Copilot API)
client = OpenAI(
    base_url="https://api.githubcopilot.com",
    api_key=os.environ.get("GITHUB_TOKEN"),
    default_headers={ ... }
)

# 로컬 LLM 전환 예시 (Ollama / LM Studio / vLLM)
client = OpenAI(
    base_url="http://localhost:11434/v1",   # Ollama
    api_key="ollama",                        # 더미 키
)
```

### 로컬 LLM 후보 및 고려사항

| 서버 | 특징 | 적합 모델 예시 |
|------|------|--------------|
| **Ollama** | 설치 간편, macOS/Linux | llama3, mistral, gemma3 |
| **LM Studio** | GUI 제공, Windows 친화적 | 동일 |
| **vLLM** | 고성능, GPU 서버용 | Llama 3.1 70B+ |
| **llama.cpp** | 경량, CPU 가능 | GGUF 포맷 모델 |

### 로컬 전환 시 예상 이점

```
현재 (API)                          로컬 LLM
─────────────────────               ─────────────────────
context window: 200k                context window: 모델 의존 (8k ~ 128k)
비용: API 토큰 과금                  비용: 전기료 + 초기 하드웨어
틱 속도: 네트워크 지연 포함           틱 속도: GPU 성능에 비례
동시 실행: API Rate Limit            동시 실행: 하드웨어 한계까지 자유
데이터 프라이버시: 외부 전송           데이터 프라이버시: 완전 로컬
세계관 크기: API 토큰 비용 제약       세계관 크기: 사실상 무제한
```

### 대규모 로어 생성 전략 (로컬 전환 후)

로컬 LLM 환경에서는 다음 확장이 현실적으로 가능해진다.

1. **엔트리 수 제한 해제** — 현재 컨텍스트 압축 전략을 유지하되,
   더 많은 엔트리를 직렬화 단계에서 카테고리별 중요도 순으로 선별하는 로직 추가

2. **병렬 틱 실행** — 독립적인 지역/세력별 서브 세계관을 별도 스레드로 동시 시뮬레이션

3. **틱 간격 단축** — API Rate Limit 없이 연속 틱 가능 (틱 당 수초 단위)

4. **모델 역할 분리** — 틱 생성용 대형 모델 + 번역/요약용 소형 모델 혼용

5. **배치 처리** — 틱 결과를 즉시 DB 반영 대신 배치로 처리해 처리량 극대화

### 최소 전환 체크리스트

- [ ] 로컬 LLM 서버 설치 및 OpenAI 호환 API 활성화 확인
- [ ] `.env`에 `LLM_MODEL=모델명` 설정 (예: `llama3.2:latest`)
- [ ] `get_llm_client()` base_url / api_key 수정
- [ ] 모델의 실제 context window에 맞게 `CONTEXT_SUMMARY_THRESHOLD` 조정
- [ ] JSON 출력 안정성 확인 (로컬 모델은 JSON 준수율이 낮을 수 있음)
  - 필요 시 `_parse_json_safe()` 복구 로직 강화 또는 `format: json` 파라미터 추가

---

## 8. 파일 구조

```
worldllm/
├── app.py                    # Flask 서버, 라우트, API 엔드포인트
├── models.py                 # SQLAlchemy 모델 (6개 테이블)
├── llm_client.py             # LLM 통신, 프롬프트 템플릿, 직렬화
├── simulation_engine.py      # 틱 루프, 유저 엔트리 보호, 자동 요약
├── .env                      # GITHUB_TOKEN, LLM_MODEL, SECRET_KEY
├── instance/
│   └── worldbuilding.db      # SQLite 데이터베이스
├── templates/
│   ├── base.html             # 공통 레이아웃, 네비게이션, 마스터 설정 모달
│   ├── index.html            # 세계관 DB 관리 (CRUD, 멀티선택, LLM 생성)
│   ├── simulation.html       # 시뮬레이션 설정 및 실행 제어
│   ├── graph.html            # 엔트리 관계도 (vis.js Network)
│   ├── logs.html             # 틱 로그 뷰어
│   └── export.html           # 연구 데이터 내보내기 (JSON/CSV)
└── docs/
    ├── prompt_structure.md   # 프롬프트 구조 상세 문서
    └── system_architecture.md  # 본 문서
```
