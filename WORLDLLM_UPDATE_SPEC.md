# WorldLLM 업데이트 지시서 — 메타데이터 스키마 & 소설 모드

> 이 문서는 `PIPELINE_GUIDE.md`를 전제로 한 확장 스펙입니다. 구현 중 실제 제약(로컬 LLM 성능, SQLite 락 이슈 등)에 따라 이 문서 내용을 계속 수정하며 사용합니다.
> 기존 원칙 계승: **제안 → 사용자 승인 → 저장**, **임베딩은 optional(꺼져도 키워드 RAG로 동작)**, **원본 엔트리는 직접 수정하지 않고 파생 버전 생성**.

---

## 0. 이번 업데이트 스코프 요약

| 구분 | 내용 |
|---|---|
| 신규 메뉴 | `메타데이터 설정`, `소설` |
| 신규 테이블 | `world_attribute_schema`, `world_skill_registry`, `entry_skill_link`, `world_guideline`, `world_entry_template`, `novel_chapter`, `novel_entity_mention`, `entry_reveal_state`, `world_writing_style` |
| 기존 파이프라인 변경 | 상세 생성(가이드 5장 해당) 시 템플릿·능력치 스키마·가이드라인을 프롬프트에 주입 |
| 권장 순서 | **1차: 메타데이터 설정(능력치/스킬/템플릿)** → **2차: 소설 모드(에디터/엔티티링크/스타일)** → 3차: 퍼블릭 배포(보류, 별도 문서) |

---

## 1. 메타데이터 설정 메뉴

상단 메뉴 `마스터 설정` 옆에 신규 배치. 세계관(World) 단위로 아래 3개 서브탭 구성.

### 1-1. 능력치 스키마 (닫힌 구조)

**목적:** D&D식 6분류(STR~CHA)를 하드코딩하지 않고, 세계관마다 축 이름·개수·티어·플레이버 텍스트를 자유 정의.

```sql
CREATE TABLE world_attribute_schema (
    id INTEGER PRIMARY KEY,
    world_id INTEGER NOT NULL,
    axis_name TEXT NOT NULL,          -- 예: "힘", "판단력", "사회성"
    axis_order INTEGER,               -- 표시 순서
    min_tier INTEGER DEFAULT 1,
    max_tier INTEGER DEFAULT 5,
    tier_labels_json TEXT,            -- {"1": "영유아 같은 힘", "2": "숟가락은 들 수 있음", ...}
    is_active BOOLEAN DEFAULT 1,      -- 비활성화 시 해당 세계관에서 이 축 자체를 안 씀
    created_at TIMESTAMP
);

CREATE TABLE entry_attribute_value (
    entry_id INTEGER,
    axis_id INTEGER,                  -- FK world_attribute_schema.id
    value INTEGER,                    -- 실제 티어 값
    PRIMARY KEY (entry_id, axis_id)
);
```

**UI 요구사항**
- 엑셀/스프레드시트 그리드 (`Handsontable` 또는 `AG Grid` 권장)
- 행 = 능력치 축, 열 = 티어(1~N, N은 세계관마다 다를 수 있음)
- 셀 더블클릭 편집, 행 추가/삭제/드래그 정렬
- 축 비활성화 토글 (판타지 아닌 세계관에서 "마력" 같은 축 끄기)
- **캐릭터별 능력치 매트릭스 뷰** 별도 탭: 행=캐릭터, 열=능력치 축, 셀=현재 값. 다중 캐릭터 밸런스를 한 화면에서 비교/일괄 수정.

**LLM 생성 연동**
- 캐릭터 상세 생성 시, 이 세계관의 `world_attribute_schema` 전체(활성 축만)를 시스템 프롬프트에 주입
- LLM은 **숫자(티어)만 판단**해서 반환 → 실제 표시 텍스트는 스키마의 `tier_labels_json`에서 가져옴 (LLM이 매번 새로 톤을 창작하지 않음 → 톤 일관성 보장)
- 반환 예시: `{"힘": 2, "판단력": 4, "사회성": 1}`

---

### 1-2. 스킬/특성 레지스트리 (열린 구조)

**목적:** 능력치와 달리 캐릭터마다 새로 생겨야 하는 항목이므로, 세계관 단위 "사전"으로 관리하며 RAG로 중복을 억제하되 자율 생성은 허용.

```sql
CREATE TABLE world_skill_registry (
    id INTEGER PRIMARY KEY,
    world_id INTEGER NOT NULL,
    type TEXT CHECK(type IN ('스킬','특성')),
    name TEXT NOT NULL,
    description TEXT,
    tags_json TEXT,                   -- ["근접","원거리","사회","생존"]
    rarity TEXT,                      -- 일반/희귀/유일 (선택)
    embedding_json TEXT,              -- 선택적
    created_by TEXT,                  -- 'user' | 'llm'
    tick_created INTEGER,
    created_at TIMESTAMP
);

CREATE TABLE entry_skill_link (
    entry_id INTEGER,
    skill_id INTEGER,
    rank INTEGER,                     -- 숙련도 (선택, 능력치 스키마와 별개 수치)
    PRIMARY KEY (entry_id, skill_id)
);

CREATE TABLE world_guideline (
    world_id INTEGER PRIMARY KEY,
    skill_generation_guide TEXT,      -- 자유 텍스트, 스킬 생성 시 시스템 프롬프트에 항상 포함
    trait_generation_guide TEXT
);
```

**생성 파이프라인**
```
캐릭터 상세 생성 요청
  → world_guideline 로드 → 프롬프트 주입
  → [RAG] world_skill_registry에서 후보 검색
       - 임베딩 ON: 벡터 유사도 top-k
       - 임베딩 OFF: 태그/카테고리 키워드 매칭 (기존 원칙 계승)
  → 프롬프트: "기존 목록: [...]. 맞는 게 있으면 재사용 ID 반환,
              없으면 가이드라인에 맞춰 신규 제안"
  → LLM 출력: { reused_skill_ids: [...], new_skill_proposals: [{name, description, type, tags}] }
  → 신규 제안 → 기존 "제안 → 승인 → 저장" 원칙 적용
       → 승인 시 world_skill_registry 편입 + 임베딩 생성(옵션 시)
```

**UI 요구사항**
- 레지스트리 목록 화면 (검색/필터/태그별 정렬)
- 가이드라인 편집 텍스트박스 2개 (스킬용 / 특성용)
- 캐릭터 상세 화면에서 스킬 추가 시: "레지스트리에서 선택" + "신규 제안 요청(LLM)" 버튼 병행 제공

---

### 1-3. 엔트리 템플릿 (카테고리별 고정 양식)

**목적:** 11개 카테고리(인물/세력/장소/사건/관념/물건/종족/마법·기술/신화·종교/역사·기록/규칙·법) + 연도 각각에 표준 필드셋을 부여해 LLM 생성물의 형식을 강제.

```sql
CREATE TABLE world_entry_template (
    id INTEGER PRIMARY KEY,
    world_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    fields_json TEXT NOT NULL,        -- [{field, required(bool), description}]
    is_active BOOLEAN DEFAULT 1
);
```

**카테고리별 기본 필드 (세계관 생성 시 디폴트로 자동 삽입, 이후 세계관별 수정 가능)**

| 카테고리 | 필수 필드 | 선택 필드 |
|---|---|---|
| 인물 | 종족, 능력치(스키마 참조), 배경, 이상/유대/결점, 소속 | 스킬/특성 링크, 말투, 예시 대사, 비밀(reveal 대상) |
| 세력 | power_tier(1~5), 목표, 자원(군사/재정/정보) | 지도자, 동맹/적대, 현재 계획, 약점 |
| 장소 | danger_level(1~5), 통치 세력, 분위기 태그 | 인구, 접근 경로, 비밀, 모험 훅 |
| 사건 | 발생 틱/연도, 원인→결과 | 관련 대상, 파급범위(개인/가문/제국) |
| 관념 | 정의(한 줄), 사회적 영향력(tier) | 기원, 신봉/반대 세력, 관련 사건 |
| 물건 | 등급(일반/희귀/유일), 효과 | 제작자/기원, 소유 이력, 대가/제약 |
| 종족 | 신체 특성, 사회 구조 | 평균 수명, 서식지, 타 종족 관계, 고유 능력 |
| 마법/기술 | 희귀도, 효과 | 원리(계통), 습득 조건, 리스크/대가, 관련 스킬 링크 |
| 신화/종교 | 주신/개념, 교리 요약 | 신도 세력, 상징/의식, **진실과의 괴리**(reveal 대상) |
| 역사/기록 | 시대 구간, 신뢰도(공식/왜곡/은폐) | 기록 주체, 관련 사건/인물 |
| 규칙/법 | 적용 범위, 조항 요약 | 제정 주체, 처벌, 실효성 |
| 연도 | primary_year | `## 연도별 특기사항` (기존 구조 유지) |

**UI 요구사항**
- 카테고리 선택 → 필드 목록 편집(추가/삭제/필수 토글/라벨명 변경)
- 라벨명 변경만으로 다른 장르에 대응 가능하게 (예: "마법/기술"의 "리스크/대가" → SF 세계관에선 "부작용/조달 난이도"로 라벨만 교체)
- 이 템플릿은 **상세 생성 프롬프트(가이드 6장)** 에 자동 주입되어 필드 이탈을 방지

---

## 2. 소설 모드 (신규 메뉴)

### 2-1. 데이터 구조

```sql
CREATE TABLE novel_chapter (
    id INTEGER PRIMARY KEY,
    world_id INTEGER NOT NULL,
    title TEXT,
    order_no INTEGER,
    content TEXT,                     -- 본문 (markdown)
    status TEXT CHECK(status IN ('초안','공개')),
    reveal_chapter_ref INTEGER,       -- 이 챕터 기준 공개 범위
    created_at TIMESTAMP
);

CREATE TABLE novel_entity_mention (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER,
    entry_id INTEGER,
    span_start INTEGER,
    span_end INTEGER,
    matched_text TEXT,                -- 실제 본문 표현 (별칭일 수 있음)
    source TEXT CHECK(source IN ('keyword','llm','manual'))
);

CREATE TABLE world_writing_style (
    id INTEGER PRIMARY KEY,
    world_id INTEGER,
    novel_id INTEGER,                 -- NULL이면 세계관 기본값
    chapter_id INTEGER,               -- NULL이면 소설 전체 적용
    pov TEXT,                         -- 시점: 1인칭/3인칭 관찰자/전지적
    tone_guide TEXT,                  -- 문체 톤 자유 서술
    forbidden_expressions TEXT,       -- 금지 표현
    sample_text TEXT                  -- few-shot용 문체 샘플
);
```

`world_entry.aliases_json` 필드 추가 필요 (기존 `world_entries` 테이블 확장):
```sql
ALTER TABLE world_entries ADD COLUMN aliases_json TEXT; -- ["보바","피투성이 보바","가일런"]
```

### 2-2. 엔티티 자동 태깅 파이프라인

```
소설 본문 저장 시
  → 1차: 키워드 매칭
       title + aliases_json 전체로 트라이(trie) 인덱스 구성
       → 본문 스캔, 일치 구간마다 novel_entity_mention(source='keyword') 생성
  → 2차(옵션): LLM 보조 태깅
       대명사/문맥 지칭("그 여자", "은발의 그녀") 등 키워드 매칭이 놓친 부분
       → LLM이 후보 엔트리 제안 → 사용자 승인 시에만 등록(source='llm')
  → 사용자 수동 태깅도 상시 가능(source='manual')
```

### 2-3. 소설 LLM 생성 파이프라인

```
챕터 작성/이어쓰기 요청
  → world_writing_style 로드 (세계관 기본값 + 소설/챕터 오버라이드 병합)
  → 등장 예정 엔티티 확인 (사용자 태그 또는 LLM 문맥 추정)
       → 해당 엔트리의 stat_block + 말투/예시대사 필드를 컨텍스트로 주입
         (인물 템플릿의 "말투" 필드 → 대사 생성 시 자동 반영)
  → [RAG] 관련 과거 챕터/사건 요약 검색 (임베딩 or 키워드, 기존 옵션 원칙 유지)
  → LLM 생성 → 사용자 검수/수정
  → 저장 시 엔티티 자동 태깅 트리거 (2-2 파이프라인)
  → 본문에 기존 DB에 없는 신규 고유명사 발견 시
       → "새 엔트리로 등록하시겠습니까?" 제안 (제안→승인→저장 원칙)
```

### 2-4. 에디터 UI 요구사항

- 좌: 본문 에디터 (markdown), 우: 사이드 패널
- 인식된 엔티티는 카테고리별 색상으로 밑줄 표시 (관계도 색상 체계 재사용)
- 클릭 시 사이드 패널에 해당 엔트리 요약(stat_block + content 일부) 표시 + "관계도에서 보기" 링크
- 상단에 "글쓰기 스타일" 편집 버튼 → `world_writing_style` 편집 모달
- 챕터 목록 사이드바 (순서 변경 드래그 지원)

---

## 3. 공개 범위 관리 (스포일러 컨트롤)

소설 모드와 직접 연결되는 최소 요구사항. 퍼블릭 배포 전체 구현은 3차로 보류하되, **필드 단위 reveal 태깅**은 이번에 함께 넣는 것을 권장.

```sql
CREATE TABLE entry_reveal_state (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER,
    field_path TEXT,                  -- 예: "content.권능", "stat_block.threat_tier"
    reveal_chapter_id INTEGER,        -- 이 챕터 이후 공개
    visibility TEXT CHECK(visibility IN ('작가전용','챕터공개','완전공개'))
);
```

- 엔트리 편집 화면에서 필드별로 "공개 챕터" 지정 가능
- 소설 모드 사이드 패널은 **현재 읽는 챕터 기준으로 필터링된 정보만 표시** (작가 모드에서는 토글로 전체 정보 확인 가능)

---

## 4. 구현 순서 제안

```
Phase 1 (메타데이터 기반)
  1. world_attribute_schema 테이블 + 엑셀 그리드 편집 UI
  2. world_skill_registry + world_guideline + 생성 파이프라인
  3. world_entry_template + 상세 생성 프롬프트 연동
  4. 기존 22개 엔트리 → 신규 스키마로 역변환(LLM 보조 마이그레이션)

Phase 2 (소설 모드)
  5. novel_chapter CRUD + 기본 에디터
  6. aliases_json 추가 + 키워드 트라이 매칭 → novel_entity_mention
  7. 사이드 패널 UI + 관계도 연동
  8. world_writing_style + 스타일 프롬프트 주입 생성 파이프라인
  9. entry_reveal_state 필드 단위 공개 관리

Phase 3 (보류 — 추후 별도 문서)
  10. public_snapshot 및 퍼블릭 리더 배포
```

---

## 5. 향후 재검토 항목 (기존 13장에 추가)

7. 능력치 스키마를 세계관 템플릿(사전 프리셋)으로 공유/재사용 가능하게 할지 (예: "표준 판타지 6분류", "폴아웃식 SPECIAL" 프리셋 제공)
8. 스킬 레지스트리 임베딩 유사도 임계값(중복 판정 기준)을 세계관마다 다르게 설정할지
9. 소설 챕터 본문도 임베딩 대상에 포함해 시뮬레이션 RAG 컨텍스트로 끌어올지
10. entry_reveal_state가 세분화될수록 UI 복잡도가 올라가므로, 필드 단위 대신 "섹션 단위" reveal로 단순화할지 여부는 실사용 후 재판단
