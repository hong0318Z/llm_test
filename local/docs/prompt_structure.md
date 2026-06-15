# WorldLLM 프롬프트 구조 문서

> 본 문서는 WorldLLM 시스템이 LLM(claude-sonnet-4.5)에 전달하는 모든 프롬프트의 구조와 파라미터를 기술한다.
> 연구 재현성 확보를 위해 코드베이스(`llm_client.py`)와 동기화하여 관리한다.

---

## 1. 개요

시스템은 총 **4가지 LLM 호출 유형**을 가진다. 각각 목적과 프롬프트 구성이 다르다.

| 호출 유형 | 함수 | temperature | max_tokens | 트리거 |
|-----------|------|:-----------:|:----------:|--------|
| 틱 실행 | `run_tick()` | 0.8 | 16,000 | 매 틱마다 자동 |
| 컨텍스트 요약 | `run_summary()` | 0.3 | 16,000 | 세계관 토큰 ≥ 120,000 |
| 엔트리 번역 | `translate_entries()` | 0.3 | 16,000 | 유저 수동 호출 |
| 엔트리 생성 | `generate_entry()` | (기본값) | 2,000 | 유저 수동 호출 |

---

## 2. 틱 실행 프롬프트 (`run_tick`)

가장 핵심적인 호출. 매 틱마다 실행되며 세계관의 자율 진화를 담당한다.

### 2-1. 메시지 구조

```
messages = [
    { "role": "system", "content": <SYSTEM_PROMPT> },
    { "role": "user",   "content": <USER_PROMPT>   }
]
```

---

### 2-2. System Prompt

고정 프레임 + 유저가 시뮬레이션 설정에서 입력한 3-레벨 프롬프트로 구성된다.

```
당신은 세계관 자율 진화 엔진입니다.
주어진 세계관 엔트리들을 기반으로 논리적으로 일관된 사건과 변화를 생성합니다.

=== 세계관 기반 법칙 (Level 1) ===
{prompt_level_1}

=== 시대/맥락/현재 상황 (Level 2) ===
{prompt_level_2}

=== 틱 진행 규칙 (Level 3) ===
{prompt_level_3}

출력 규칙:
- 반드시 유효한 JSON만 출력하세요 (마크다운 코드블록 없이)
- 스키마를 정확히 따르세요
- 각 항목의 내용은 간결하게 작성하세요 (토큰 절약)

핵심 제약:
- [🔒유저] 태그가 붙은 엔트리는 절대 entry_updates나 deactivated_entries에 포함하지 마세요.
  이 엔트리들은 사용자가 설정한 세계관 코어이며 수정/비활성화가 금지됩니다.
- 유저 엔트리에서 파생된 변화를 표현해야 한다면, 반드시 new_entries로 새 엔트리를 생성하고
  references 필드에 원본 유저 엔트리 ID를 포함하세요.
```

#### 3-레벨 프롬프트 설계 의도

| 레벨 | 명칭 | 역할 | 예시 |
|------|------|------|------|
| Level 1 | 세계관 기반 법칙 | 세계의 불변 규칙 (물리 법칙, 마법 체계 등) | "이 세계에서 죽은 자는 영혼으로 남는다" |
| Level 2 | 시대/맥락/현재 상황 | 현재 시점의 정치·사회적 맥락 | "현재는 왕국 연합과 제국의 냉전 상태" |
| Level 3 | 틱 진행 규칙 | 한 틱의 시간 단위, 변화 강도, 금지 행동 | "1틱 = 1개월, 매 틱 최소 1건의 사건 발생" |

> Level 1~3이 비어있으면 각각 `"없음"` 문자열로 대체된다.

---

### 2-3. User Prompt

매 틱마다 동적으로 생성된다. 현재 세계관 상태 전체가 직렬화되어 포함된다.

```
현재 틱: {tick_number}

=== 현재 세계관 상태 ===
{world_state}

위 세계관에서 이번 틱에 발생할 사건과 변화를 JSON으로 생성하세요.

출력 JSON 스키마:
{
  "reasoning": "이번 틱의 전반적인 흐름 설명 (한국어, 3문장 이내)",
  "events": [
    {
      "type": "world_event",
      "description": "사건 설명",
      "affected_entry_ids": [1, 2]
    }
  ],
  "entry_updates": [
    {
      "id": 1,
      "new_content": "업데이트된 내용",
      "reason": "변경 이유"
    }
  ],
  "new_entries": [
    {
      "title": "새 인물/개념/사물 이름",
      "category": "인물",
      "content": "내용",
      "references": [1, 2]
    }
  ],
  "deactivated_entries": [
    {
      "id": 3,
      "reason": "소멸/사망/소실 이유"
    }
  ]
}
```

#### 출력 필드 설명

| 필드 | 타입 | 설명 |
|------|------|------|
| `reasoning` | string | LLM이 이번 틱을 어떻게 해석했는지 서술. 연구 분석용 |
| `events` | array | 세계 사건 목록. DB에 저장되나 엔트리를 직접 변경하지 않음 |
| `entry_updates` | array | 기존 LLM 엔트리 내용 업데이트. **유저 엔트리는 코드 레벨에서 차단** |
| `new_entries` | array | 새로 생성되는 엔트리. `created_by = "llm"` 으로 저장 |
| `deactivated_entries` | array | 비활성화할 엔트리. **유저 엔트리는 코드 레벨에서 차단** |

---

### 2-4. 세계관 직렬화 형식 (`world_state`)

엔트리는 카테고리별로 그룹핑되어 다음 형식으로 직렬화된다.

```
[세력]
  ID=1 | 아르곤 왕국 [🔒유저] (참조: [3, 5])
    북방의 강대국. 철기 문명을 바탕으로 500년간 유지된 군주제 국가...

  ID=7 | 암흑 동맹
    틱 2에서 생성된 반왕국 세력. 아르곤의 압제에 저항하는 조직...

[인물]
  ID=2 | 레나 공주 [🔒유저]
    아르곤 왕국의 외동딸. 마법 재능을 가진 외교적 인물...
```

**직렬화 규칙:**

- `[🔒유저]` 태그: `created_by == "user"` 인 엔트리에 자동 부여
- 내용 길이 초과 시: `…(+N자 생략)` 표시 (마스터 설정의 `max_llm_entry_chars` 기준)
- 카테고리 6종으로 그룹핑: 세력 / 인물 / 관념 / 물건 / 종족 / 사건
- 제외 조건: `is_active = False` 또는 `is_summarized = True` 인 엔트리

---

## 3. 컨텍스트 요약 프롬프트 (`run_summary`)

### 3-1. 트리거 조건

직렬화된 `world_state`의 추정 토큰 수가 **120,000 이상**이 되면 해당 틱 실행 전에 자동으로 먼저 실행된다.

```
토큰 추정: int(len(world_state) / 1.5)  ← 한국어 기준 1토큰 ≈ 1.5자
```

### 3-2. 메시지 구조

System 메시지 없이 user 단일 메시지만 사용한다.

```
messages = [
    { "role": "user", "content": <SUMMARY_PROMPT> }
]
```

### 3-3. Prompt

```
다음은 현재까지 축적된 세계관 엔트리 전체입니다.
컨텍스트 한계에 근접했으므로, 이 세계관을 카테고리별로 압축 요약해주세요.

=== 전체 세계관 ===
{world_state}

출력 JSON 스키마:
{
  "reasoning": "요약 수행 이유 및 요약 방침",
  "summaries": [
    {
      "category": "카테고리명",
      "title": "요약 제목 (예: '세력 전체 요약 - 틱 N')",
      "content": "해당 카테고리의 모든 엔트리를 포함한 압축 요약",
      "covered_entry_ids": [1, 2, 3]
    }
  ]
}
```

### 3-4. 요약 후 처리

- 요약 결과는 새 엔트리(`created_by = "llm"`)로 DB에 저장됨
- `covered_entry_ids`에 해당하는 원본 엔트리는 `is_summarized = True`로 마킹
- 이후 틱부터 원본 엔트리는 직렬화에서 제외되고 요약 엔트리만 포함됨

---

## 4. 번역 프롬프트 (`translate_entries`)

유저가 DB에서 엔트리를 선택해 수동으로 호출한다. 연구 데이터의 영문 병기용.

```
messages = [
    { "role": "user", "content": <TRANSLATE_PROMPT> }
]
```

```
아래 세계관 엔트리들을 영어로 번역하세요.
제목과 내용 모두 자연스러운 영어로 번역하되, 고유명사는 원문을 병기하세요.

=== 번역할 엔트리 ===
ID=1 [세력] 아르곤 왕국
  북방의 강대국...          ← 내용 최대 400자로 전달

출력 JSON 스키마:
{
  "translations": [
    {
      "id": 1,
      "title_en": "English Title",
      "content_en": "English content..."
    }
  ]
}
```

번역 결과는 동일 카테고리의 새 엔트리로 저장되며 원본과 함께 공존한다.

---

## 5. 엔트리 생성 프롬프트 (`generate_entry`)

유저가 DB 입력 모달에서 제목·분류·힌트를 입력한 뒤 수동으로 호출한다.

```
messages = [
    { "role": "user", "content": <GENERATE_PROMPT> }
]
```

```
다음 정보를 바탕으로 세계관 엔트리의 상세 내용을 작성해주세요.

제목: {title}
분류: {category}
사용자 힌트/초안:           ← 유저가 내용란에 미리 쓴 텍스트 (없으면 생략)
{hint}
참조 엔트리:                ← 참조 ID 입력 시 해당 엔트리 내용 300자 포함 (없으면 생략)
  [세력] 아르곤 왕국: ...

요구 사항:
- 세계관 설정에 어울리는 구체적이고 풍부한 묘사
- 참조 엔트리와 자연스럽게 연결되는 내용
- 마크다운 기호(**볼드**, # 헤더 등) 사용 금지, 일반 텍스트만
- 한국어로 작성
- 반드시 JSON으로만 응답: {"content": "생성된 내용"}
```

---

## 6. 파라미터 요약

| 파라미터 | 값 | 비고 |
|---------|-----|------|
| 모델 | `claude-sonnet-4.5` | `LLM_MODEL` 환경변수로 오버라이드 가능 |
| API 엔드포인트 | `https://api.githubcopilot.com` | GitHub Copilot API (OpenAI 호환) |
| Context window | 200,000 토큰 | |
| max_tokens (틱 / 요약 / 번역) | 16,000 | |
| max_tokens (엔트리 생성) | 2,000 | |
| temperature (틱) | 0.8 | 창의성·다양성 중시 |
| temperature (요약 / 번역) | 0.3 | 일관성·정확성 중시 |
| 자동 요약 임계값 | 120,000 토큰 | `CONTEXT_SUMMARY_THRESHOLD` |
| 토큰 추정식 | `int(len(text) / 1.5)` | 한국어 기준 (실제 토큰과 근사) |
| LLM 엔트리 최대 글자 수 | 마스터 설정값 (기본 500) | 직렬화 시 초과분 `…(+N자 생략)` |

---

## 7. 응답 처리 파이프라인

모든 LLM 응답은 `_parse_json_safe()` 를 거쳐 파싱된다.

```
LLM 응답 (raw string)
    │
    ▼
마크다운 코드블록 제거 (``` 로 시작하면 첫 줄·마지막 ``` 제거)
    │
    ▼
json.loads() 시도
    │
    ├─ 성공 → 결과 반환
    │
    └─ 실패 (JSONDecodeError)
          │
          ▼
       열린 "{" 수만큼 "}" 추가 후 재시도   ← 응답 잘림(length) 복구
          │
          ├─ 성공 → 결과 반환
          │
          └─ 실패
                │
                ▼
             fallback 빈 결과 반환
             { "_parse_error": true, "events": [], "entry_updates": [], ... }
```

`finish_reason == "length"` 이면 `_truncated: true` 플래그가 추가로 세팅되고
`reasoning` 필드 앞에 `[응답 잘림]` 접두어가 붙는다.

---

## 8. 유저 엔트리 보호 메커니즘

연구 무결성 확보를 위해 사용자가 입력한 세계관 코어(seed) 엔트리가 LLM에 의해 변조되는 것을 이중으로 방지한다.

### 1차 방어 — 프롬프트 레벨

직렬화 시 유저 엔트리에 `[🔒유저]` 태그를 부여하고, system prompt에 명시적 금지 규칙을 포함시켜 LLM이 스스로 해당 엔트리를 회피하도록 유도한다.

### 2차 방어 — 코드 레벨 (`simulation_engine.py`)

LLM 응답이 프롬프트 제약을 무시하더라도 엔진이 강제 차단한다.

| LLM 시도 | 코드 처리 |
|---------|---------|
| 유저 엔트리 `entry_updates` | 수정 차단 → `"{원본제목} (변화 - 틱 N)"` 새 LLM 엔트리 생성, `references`에 원본 ID 연결 |
| 유저 엔트리 `deactivated_entries` | 비활성화 차단 → `entry_protected` 이벤트 로그만 기록 |

---

## 9. 소스 코드 참조

| 항목 | 파일 | 위치 |
|------|------|------|
| 프롬프트 템플릿 전체 | `llm_client.py` | `SYSTEM_PROMPT_TEMPLATE`, `USER_PROMPT_TEMPLATE`, `SUMMARY_PROMPT_TEMPLATE`, `TRANSLATE_PROMPT_TEMPLATE` |
| 세계관 직렬화 | `llm_client.py` | `serialize_world_state()` |
| 틱 실행 | `llm_client.py` | `run_tick()` |
| 컨텍스트 요약 | `llm_client.py` | `run_summary()` |
| 엔트리 생성 | `llm_client.py` | `generate_entry()` |
| 유저 엔트리 보호 | `simulation_engine.py` | `_apply_tick_result()` |
| 자동 요약 트리거 | `simulation_engine.py` | `run_simulation()` 내 `needs_summary()` 호출부 |
