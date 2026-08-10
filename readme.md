# WorldLLM

WorldLLM은 세계관 데이터베이스, TRPG/D&D형 메타데이터, LLM 기반 세계관 설계·시뮬레이션, 타임라인, 관계도와 소설 집필을 한곳에서 관리하는 Flask 웹 애플리케이션입니다.

핵심 원칙은 다음과 같습니다.

- LLM 결과는 가능한 한 **제안 → 사용자 확인 → DB 저장** 순서로 처리합니다.
- 세계관 엔트리와 LLM 연결 설정을 분리합니다.
- 로컬 LLM에서 JSON 오류가 줄도록 큰 작업을 작은 단위로 나눕니다.
- 비밀 정보는 일반 엔트리 조회와 소설 공개 화면에서 자동 노출하지 않습니다.
- 소설은 이야기/부에서 선택한 DB 정보만 LLM 컨텍스트로 사용합니다.

## 주요 기능

### 세계관 DB

- 지원 분류: 세력, 인물, 관념, 물건, 종족, 사건, 장소, 마법/기술, 신화/종교, 역사/기록, 규칙/법, 연도
- Markdown 엔트리 작성과 안전한 HTML 렌더링
- 대표 연도와 연도별 특기사항 분리
- 엔트리 참조, 관계 유형, 관계 설명과 관계도
- 제목 변경·별칭·동일 인물 연결
- 인물의 아버지, 어머니, 자식과 재귀 가계도
- 물건의 대표 소유자 복수 지정 및 검색 순위
- 공개 본문과 별도로 저장되는 비밀 정보
- 유저 원본과 LLM 파생 버전 관리
- 키워드, 자동 태그, 선택적 임베딩과 의미 검색
- 이미지 업로드 및 NovelAI 이미지 생성

### 메타데이터와 캐릭터 시트

- 세계관별 능력치 축, 최소·최대 단계, 축 설명
- 각 정수 단계의 행동 범위와 판정 한계 서술
- 활성 능력치 행별 `빈칸 LLM 채우기`
- 기존 설명을 덮어쓰지 않고 비어 있는 설명·단계만 생성
- 캐릭터 매트릭스에서 인물 한 명씩 능력치 자동 배정
- 능력치 수치와 인물별 근거를 D&D형 상세 시트에 표시
- 세계관별 스킬/특성 레지스트리와 생성 가이드
- 카테고리별 엔트리 템플릿

### 세계관 설계와 아이디어 대화

- 큰 맥락에서 설계 목록 생성
- 기존 DB 중복 검사 후 선택 항목만 상세 생성
- 로컬 LLM을 위한 분할 생성
- 생성 결과 검토 후 선택 저장
- 아이디어 대화에서 Markdown 응답 표시
- 대화에서 확정된 내용을 엔트리 초안으로 변환

### 시뮬레이션과 타임라인

- 3단계 시뮬레이션 프롬프트와 틱 설정
- 유저 원본 보호 및 파생 버전 생성
- 틱별 사건, 생성·수정·비활성화 로그
- 키워드/임베딩 RAG와 컨텍스트 요약
- 타임라인, 사건, 스토리 비트 관리
- 실행 취소, 스냅샷, 실행 결과 내보내기

### 소설

- 이야기/부 → 챕터 계층
- 이야기/부별 참조 DB 검색·선택
- 같은 부의 챕터가 상위 DB 선택을 상속
- 선택된 DB만 LLM 생성, 자동 인식, 보조·수동 태깅에 사용
- 같은 부의 관련 과거 챕터만 최대 3개 참조
- 마스터 설정의 전역 시점·톤·금지 표현·샘플 문체 적용
- 이어쓰기/수정 제안 후 본문 적용
- 신규 고유명사 후보 검토 후 엔트리 등록
- 엔티티 공개 범위: 작가전용, 챕터공개, 완전공개
- 부 전체 또는 챕터 단위 로그인 없는 공개 링크

## 시스템 구성

```text
브라우저
  └─ Flask UI / JSON API
       ├─ SQLite
       │   ├─ 사용자와 사용자별 LLM 연결 설정
       │   ├─ 세계관, 엔트리, 관계, 메타데이터
       │   ├─ 소설, 타임라인, 시뮬레이션, 로그
       │   └─ 스냅샷과 공개 범위
       ├─ OpenAI 호환 Chat Completions
       │   ├─ Ollama / LM Studio / vLLM
       │   ├─ OpenAI / DeepSeek / OpenRouter
       │   └─ GitHub Copilot API fallback
       ├─ 선택적 OpenAI 호환 Embeddings
       └─ 선택적 NovelAI 이미지 API
```

## 설정 범위와 우선순위

설정이 적용되는 범위를 혼동하지 않도록 다음 계층을 기준으로 사용합니다.

| 범위 | 설정 위치 | 적용 내용 |
|---|---|---|
| 서버 시작 환경 | `.env` | 포트, DB URL, Flask 시크릿, UI에 저장된 연결값이 없을 때 쓸 LLM fallback |
| 글로벌 마스터 | 상단 `마스터 설정` | 출력/글자 제한, 틱 생성 수, RAG, 소설 전역 문체 |
| 사용자별 연결 | `마스터 설정` 내부 연결 항목 | LLM/임베딩 주소, API 키, 모델, NovelAI 키 |
| 세계관별 | 메타데이터·프롬프트·시뮬레이션 설정 | 능력치, 스킬, 템플릿, 세계관 규칙과 틱 프롬프트 |
| 이야기/부별 | 소설의 상위 분류 편집기 | 챕터가 참조할 DB 엔트리, 이야기 설명 |
| 챕터별 | 소설 챕터 편집기 | 제목, 본문, 상태, 공개 링크 |

LLM 연결은 일반적으로 다음 우선순위를 사용합니다.

```text
로그인 사용자의 DB 연결 설정
  → .env의 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
  → GitHub Copilot 기본 endpoint와 GITHUB_TOKEN
```

`LLM_MAX_OUTPUT_TOKENS` 환경변수가 있으면 마스터 설정의 최대 출력 토큰보다 우선합니다. 환경변수가 없으면 마스터 설정값을 사용하며 애플리케이션 상한은 262144입니다. 실제 허용 길이는 연결한 모델과 서버의 컨텍스트 한도를 따릅니다.

### 마스터 설정

| 항목 | 의미 |
|---|---|
| LLM 생성 엔트리 최대 글자 수 | 엔트리 생성·참조 시 항목별 길이. `0`은 애플리케이션 제한 없음 |
| LLM 응답 최대 토큰 | `chat.completions`에 전달하는 `max_tokens`, 최대 262144 |
| 틱당 새 엔트리 생성 수 | 시뮬레이션 한 틱의 신규 엔트리 최대 수. `0`은 모델 판단 |
| 유저 입력 최대 글자 수 | 입력 UI 가이드. 저장 자체를 막지는 않음 |
| RAG 토큰 예산 | 시뮬레이션 컨텍스트 축소 기준. `0`은 RAG 예산 필터 비활성화 |
| 소설 전역 문체 | 모든 부와 챕터에 적용되는 시점, 톤, 금지 표현, 샘플 문체 |
| 임베딩 설정 | 벡터 검색 사용 여부, endpoint, 모델, 참조 수 |
| LLM 연결 | 제공자, OpenAI 호환 주소, 모델과 사용자별 API 키 |

API 키는 화면 응답에서 원문을 다시 보여주지 않지만 현재 DB 자체를 암호화하는 기능은 없습니다. NAS 계정, 파일 권한과 외부 접근을 함께 보호해야 합니다.

## 기능 파이프라인

### 1. 세계관 설계

```text
큰 맥락과 추가 답변
  → 설계 목록 생성
  → 기존 제목 및 선택적 임베딩 중복 검사
  → 사용자가 생성할 항목 선택
  → 작은 배치로 상세 내용 생성
  → 미리보기
  → 승인한 항목만 DB 저장
```

세계관 설계의 상세 생성은 마스터 설정의 엔트리 최대 글자 수와 세계관별 템플릿·메타데이터를 참고합니다.

### 2. 엔트리 생성·수정

```text
사용자 입력 또는 LLM 초안
  → 카테고리 템플릿과 활성 능력치 확인
  → 제목·본문·연도·참조·비밀 분리
  → 사용자 승인
  → DB 저장
  → 키워드/자동 태그 생성
  → 임베딩 활성 시 벡터 저장
```

AI 수정은 기존 엔트리를 즉시 변경하지 않습니다. 질문과 수정안을 대화로 확정한 뒤 `저장`을 승인해야 반영됩니다.

### 3. 능력치 스키마와 캐릭터 배정

```text
활성 능력치 행 선택
  → 해당 행에서 비어 있는 축 설명·단계만 추출
  → LLM에 한 행만 요청
  → 누락 필드만 한 번 재요청
  → 기존 값은 유지하고 빈칸만 DB 저장

캐릭터 매트릭스에서 인물 한 명 선택
  → 인물 본문 + 활성 능력치만 전달
  → 스키마를 재출력하지 않고 수치·근거만 생성
  → 완전한 응답인지 확인
  → 사용자 승인 후 그 인물의 값 저장
```

이 구조는 여러 축의 수십 개 단계와 여러 캐릭터를 한 JSON으로 생성할 때 발생하던 로컬 LLM 형식 오류를 줄이기 위한 것입니다.

### 4. 시뮬레이션

```text
실행 설정과 틱 시작
  → 현재 세계관, 스토리 비트, 최근 로그 수집
  → 필요 시 컨텍스트 요약
  → RAG 예산에 따라 관련 엔트리 선택
  → LLM 틱 결과 생성
  → 유저 원본 보호 규칙 검사
  → 사건·파생 엔트리·버전·비활성 상태 저장
  → 로그와 통계 기록
```

유저가 직접 만든 코어 엔트리는 시뮬레이션이 직접 덮어쓰지 않습니다. 변화가 필요하면 새 파생 엔트리나 버전으로 기록합니다.

### 5. 소설

```text
마스터 설정에서 전역 문체 저장
  → 이야기/부 생성
  → 상위 분류에서 참조 DB 검색·선택 후 부 저장
  → 챕터 작성 또는 LLM 요청
  → 선택된 DB + 능력치/스킬 + 부 설명 수집
  → 같은 부의 관련 과거 챕터 최대 3개 선택
  → LLM이 Markdown 본문과 신규 고유명사 후보 제안
  → 사용자가 본문 적용 및 신규 엔트리 등록 승인
  → 챕터 저장 시 선택 DB 안에서 엔티티 인식
```

중요한 동작:

- 기존 이야기/부의 선택 목록은 초기에는 비어 있습니다.
- 선택 목록이 비어 있으면 소설 기능은 세계관 DB를 LLM에 전달하지 않습니다.
- 선택하지 않은 엔트리는 자동 태깅, LLM 보조 태깅, 수동 태깅 후보에도 나타나지 않습니다.
- 문체는 마스터 설정 한 곳에서만 관리하며 챕터별 덮어쓰기는 사용하지 않습니다.
- LLM 제안은 자동 저장되지 않습니다.

### 6. 공개와 비밀

```text
엔트리 필드 공개 범위 설정
  ├─ 작가전용: 공개 독자에게 숨김
  ├─ 챕터공개: 지정 챕터 순서부터 공개
  └─ 완전공개: 즉시 공개 가능

부/챕터 공개 링크 생성
  → 로그인 없는 읽기 전용 페이지
  → 현재 공개 시점에 허용된 DB 정보만 사이드 패널에 표시
```

엔트리의 `secret_content`는 별도 endpoint로만 조회하며 일반 조회, LLM 일반 컨텍스트와 공개 페이지에 포함하지 않습니다.

### 7. 백업·복원

세계관별 JSON 백업에는 다음 확장 정보가 포함됩니다.

- 능력치 스키마와 캐릭터 값
- 스킬/특성 레지스트리와 연결
- 템플릿과 가이드라인
- 관계, 동일인물·가계도·소유자 연결에 사용되는 엔트리 참조
- 이야기/부, 부별 DB 선택, 챕터와 엔티티 mention
- 공개 범위와 비밀 정보

복원 시 엔트리·부·챕터 ID를 새 DB ID로 재매핑합니다. 사용자별 API 키는 세계관 백업에 포함하지 않습니다.

## 설치

### 요구 사항

- Python 3.10 이상. Docker 이미지는 Python 3.12 사용
- Git
- 로컬 실행 시 Python 가상환경 권장
- Docker 배포 시 Docker Compose 또는 Synology Container Manager
- OpenAI 호환 LLM endpoint 또는 GitHub Copilot 접근 토큰

### 저장소 받기

```powershell
git clone https://github.com/hong0318Z/llm_test.git
cd llm_test
git checkout claude/worldbuilding-llm-system-fQCoJ
```

### 로컬 실행 — PowerShell

```powershell
python -m venv venv
Set-ExecutionPolicy -Scope Process Bypass
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

기본 로컬 주소는 `.env`의 `PORT`를 따릅니다. 현재 예제는 `http://localhost:4318`입니다.

## 환경변수

`.env.example`을 `.env`로 복사한 뒤 필요한 값만 수정합니다.

```env
# 세션 서명. 운영 환경에서는 반드시 긴 임의 문자열로 변경
SECRET_KEY=change-this-in-production

# 서버와 DB
PORT=4318
# 로컬 기본값을 쓰지 않을 때만 지정
# DATABASE_URL=sqlite:///worldbuilding.db

# OpenAI 호환 로컬/원격 LLM
LLM_BASE_URL=http://192.168.0.10:11434/v1
LLM_API_KEY=local-no-key
LLM_MODEL=qwen3:8b
# 선택: 마스터 설정보다 우선하는 출력 상한
# LLM_MAX_OUTPUT_TOKENS=262144

# LLM_BASE_URL이 없을 때 GitHub Copilot fallback
# GITHUB_TOKEN=github_token

# 선택적 임베딩 endpoint
EMBEDDING_BASE_URL=http://192.168.0.10:11434/v1
EMBEDDING_API_KEY=local-no-key

# 선택적 NovelAI 이미지 생성
# NOVELAI_API_KEY=novelai_token
```

임베딩 모델명과 사용 여부는 마스터 설정에서 지정합니다. `.env`는 Git에 커밋하지 마세요.

## 최초 로그인과 사용자 승인

첫 시작 시 코드가 초기 관리자 계정을 자동 생성합니다.

```text
아이디: admin
초기 비밀번호: kevin0318
```

이 값은 공개 저장소에 포함된 초기값이므로 외부에 서비스를 공개하기 전에 반드시 별도의 관리자 비밀번호 정책을 적용하고 접근을 제한해야 합니다. 일반 사용자는 가입 후 승인 대기 상태가 되며 관리자가 `사용자 승인` 화면에서 승인해야 로그인할 수 있습니다.

LLM 주소·API 키·모델은 사용자별로 저장되지만 세계관 데이터는 선택한 세계관을 사용하는 승인 사용자 사이에서 공유됩니다.

## Docker / Synology 배포

`docker-compose.yml`의 기본 구성:

| 항목 | 값 |
|---|---|
| 서비스/컨테이너 | `worldllm` |
| 외부 포트 | `4318` |
| Gunicorn | worker 1, threads 4, timeout 300초 |
| SQLite | `./data/worldbuilding.db` → `/data/worldbuilding.db` |
| 이미지 | `./storage` → `/app/storage` |

최초 실행:

```bash
cp .env.example .env
docker compose up -d --build worldllm
docker compose ps
docker compose logs --tail=100 worldllm
```

Synology에서 LLM이 다른 장비에 있다면 `host.docker.internal`보다 해당 장비의 LAN IP를 사용하는 편이 안정적입니다. LLM endpoint를 인터넷에 직접 공개할 필요는 없습니다.

업데이트:

```bash
cd /volume4/llm_lore_create/llm_test
git pull --ff-only origin claude/worldbuilding-llm-system-fQCoJ
docker compose up -d --build worldllm
docker compose ps
docker compose logs --tail=100 worldllm
```

애플리케이션 시작 시 필요한 신규 SQLite 컬럼을 자동 추가합니다. 업데이트 전에 `.env`, `data/worldbuilding.db`, `storage/`를 백업하세요.

## 데이터 위치

| 실행 방식 | DB | 이미지 |
|---|---|---|
| 로컬 기본 | `instance/worldbuilding.db` | `storage/` |
| Docker | `data/worldbuilding.db` | `storage/` |

세계관 JSON 백업과 별도로 실제 SQLite 파일도 정기적으로 백업하는 것을 권장합니다.

## 검증

외부 LLM 호출 없이 핵심 업데이트 사양을 검사합니다.

```powershell
python -m unittest tests.test_update_spec -v
python -m py_compile app.py models.py llm_client.py simulation_engine.py
git diff --check
```

현재 회귀 테스트는 다음을 포함합니다.

- 메타데이터 단계 및 캐릭터별 값 저장
- 행별 빈칸 생성과 기존 값 보존
- 한 캐릭터 단위 자동 배정 범위
- 비밀, 동일 인물, 가계도와 소유자
- 소설 부별 DB 선택과 생성 컨텍스트 제한
- 전역 소설 문체
- 로그인 없는 부/챕터 공개
- 백업·복원 ID 재매핑과 삭제 무결성

## 문제 해결

### LLM 연결 실패

1. 상단 `LLM 연결 테스트`에서 endpoint와 모델을 확인합니다.
2. 로컬 LLM 주소가 `/v1`을 포함하는지 확인합니다.
3. Docker 컨테이너에서 해당 LAN IP와 포트에 접근 가능한지 확인합니다.
4. 모델 목록 endpoint를 지원하지 않으면 모델명을 직접 입력합니다.

### JSON 파싱 또는 단계 누락

- 로컬 모델이 코드블록, 잘못된 키, 깨진 문자를 출력하면 전체 JSON이 무효가 될 수 있습니다.
- 능력치 스키마는 전체 자동 생성 대신 각 활성 행의 `빈칸 LLM 채우기`를 사용합니다.
- 캐릭터 수치는 매트릭스에서 인물 한 명씩 자동 배정합니다.
- 작업 진단 패널의 원문 미리보기에서 실제 LLM 응답을 확인합니다.

### 소설이 DB 정보를 참고하지 않음

1. 챕터의 상위 이야기/부를 선택합니다.
2. `챕터가 참조할 DB 정보`에서 엔트리를 검색해 추가합니다.
3. 반드시 `부 저장`을 누릅니다.
4. 선택 수가 `DB N개`로 표시되는지 확인합니다.

### 포트 충돌

`.env`에서 `PORT`를 바꿉니다. Docker 외부 포트를 바꾸려면 `docker-compose.yml`의 왼쪽 포트도 함께 수정합니다.

### 업데이트 후 화면이 이전 상태로 보임

브라우저에서 `Ctrl+F5`로 캐시를 무시하고 새로고침합니다.

## 디렉터리 구조

```text
llm_test/
├─ app.py                   Flask 화면·API와 자동 DB 컬럼 마이그레이션
├─ models.py                SQLAlchemy 데이터 모델
├─ llm_client.py            LLM/임베딩 클라이언트와 생성 파이프라인
├─ simulation_engine.py     틱 시뮬레이션 실행기
├─ run.py                   로컬 개발 실행 진입점
├─ Dockerfile
├─ docker-compose.yml
├─ requirements.txt
├─ templates/
│  ├─ base.html             공통 UI와 마스터 설정
│  ├─ index.html            세계관 DB와 D&D형 상세 보기
│  ├─ metadata.html         능력치·스킬·템플릿
│  ├─ novel.html            이야기/부·챕터 편집기
│  ├─ public_novel.html     로그인 없는 공개 읽기
│  ├─ simulation.html       시뮬레이션 실행
│  ├─ timeline.html         타임라인과 스토리 비트
│  ├─ graph.html            관계도와 가계도
│  ├─ chat.html             아이디어 대화
│  └─ world_design.html     단계형 세계관 설계
├─ tests/test_update_spec.py
├─ data/                    Docker SQLite 데이터, Git 제외
├─ storage/                 업로드·생성 이미지, Git 제외
└─ .env                     비밀 환경변수, Git 제외
```

추가 운영 문서는 [PIPELINE_GUIDE.md](PIPELINE_GUIDE.md), [ARCHITECTURE.md](ARCHITECTURE.md), [SYNOLOGY_DEPLOY.md](SYNOLOGY_DEPLOY.md)를 참고하세요.
