# WorldLLM Synology 배포 및 Git 운영 가이드

이 문서는 Docker/Container Manager가 설치된 Synology NAS에서 WorldLLM을 운영하는 절차입니다.

## 1. 로컬 변경 사항을 GitHub에 올리기

프로젝트 루트에서 아래를 실행합니다. `.env`, `data/`, `storage/`에는 비밀 값과 실제 데이터가 있으므로 Git에 올리지 않습니다.

```bash
git status
git add .env.example Dockerfile docker-compose.yml .dockerignore \
  app.py llm_client.py models.py simulation_engine.py \
  templates/base.html templates/chat.html readme.md SYNOLOGY_DEPLOY.md
git commit -m "Add Synology Docker, local LLM, embeddings and chat"
git push origin HEAD
```

현재 브랜치 확인 및 원격 저장소 확인 명령:

```bash
git branch --show-current
git remote -v
```

`git push`가 거절되면 먼저 원격 변경을 반영합니다.

```bash
git pull --rebase origin "$(git branch --show-current)"
git push origin HEAD
```

## 2. NAS에 최초 설치

Synology에서 SSH를 활성화한 후 SSH로 접속합니다. Git과 Container Manager(Docker)가 설치되어 있어야 합니다.

```bash
ssh NAS_사용자@NAS_IP
mkdir -p /volume4/llm_lore_create
cd /volume4/llm_lore_create
git clone GITHUB_REPOSITORY_URL .
cp .env.example .env
```

`.env`를 열어 로컬 LLM 주소와 시크릿을 설정합니다.

```env
PORT=4318
SECRET_KEY=충분히_긴_랜덤_문자열
LLM_BASE_URL=http://OLLAMA가_실행중인_장비_IP:11434/v1
LLM_API_KEY=local-no-key
LLM_MODEL=qwen3:8b
EMBEDDING_BASE_URL=http://OLLAMA가_실행중인_장비_IP:11434/v1
EMBEDDING_MODEL=nomic-embed-text
```

같은 NAS에 Ollama를 Docker로 함께 설치했다면 `LLM_BASE_URL`에는 해당 Ollama 컨테이너 이름과 같은 Docker 네트워크를 사용하거나 NAS LAN IP를 지정합니다. `host.docker.internal`은 Synology Linux 환경에서 항상 보장되지 않으므로 LAN IP가 더 안정적입니다.

컨테이너를 빌드하고 시작합니다.

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f worldllm
```

브라우저에서 `http://NAS_IP:4318`로 접속합니다.

데이터는 아래 경로에 영속적으로 저장됩니다.

- SQLite DB: `/volume4/llm_lore_create/data/worldbuilding.db`
- 업로드/생성 이미지: `/volume4/llm_lore_create/storage/`
- 비밀 설정: `/volume4/llm_lore_create/.env`

## 3. 이후 Git 업데이트 배포

NAS에서 실행합니다. 데이터 디렉터리와 `.env`를 지우지 마세요.

```bash
cd /volume4/llm_lore_create
git pull --ff-only
docker compose up -d --build
docker image prune -f
```

정상 여부 확인:

```bash
docker compose ps
docker compose logs --tail=100 worldllm
```

## 4. 임베딩 및 로컬 LLM 사용

앱의 **마스터 설정**에서 LLM OpenAI 호환 주소, 모델명, 필요 시 API 키를 직접 저장할 수 있습니다. 같은 화면에서 `임베딩 기반 벡터 검색 사용`을 켜고 임베딩 주소·모델명·키와 틱당 참조 수를 설정합니다. 키는 NAS 내부 DB에 저장되며 화면에서는 저장 여부만 표시됩니다. 임베딩은 기본적으로 꺼져 있으므로, 활성화하기 전에는 기존 키워드 기반 RAG만 동작합니다.

새로 저장하거나 수정하는 엔트리는 자동 태그/임베딩 대상이 됩니다. 임베딩 기능은 OpenAI 호환 `/v1/embeddings` endpoint를 제공하는 Ollama, LM Studio, vLLM 등에 연결할 수 있습니다.

## 5. 운영 주의 사항

- `.env`는 절대 커밋하지 않습니다.
- NAS 외부 공개 시 Synology Reverse Proxy와 HTTPS, 인증을 추가하세요.
- SQLite는 단일 컨테이너/단일 worker 운영에 적합합니다. 현재 Docker 설정은 이를 위해 Gunicorn worker를 1개로 고정했습니다.
- 업데이트 전 `data/worldbuilding.db`와 `storage/`를 Hyper Backup 등으로 백업하세요.
