# 반드시 읽기: 업데이트·Git·Synology 배포 명령

> 이 프로젝트를 수정하거나 NAS에 배포하기 전에 반드시 이 문서를 확인하세요.

## 매 업데이트 후 안내할 PowerShell 명령

모든 기능 업데이트 완료 시 아래 형식의 명령을 함께 제공합니다. 실제 변경 파일 목록은 업데이트 내용에 맞춰 조정합니다.

```powershell
# 프로젝트 폴더로 이동
cd "C:\Users\HongjunChoi\Downloads\llm_test-claude-worldbuilding-llm-system-fQCoJ\llm_test"

# 현재 브랜치 확인
git branch --show-current

# 변경 파일 추가
git add app.py models.py llm_client.py simulation_engine.py requirements.txt `
  templates/base.html templates/index.html

# 변경 내용을 설명하는 메시지로 커밋
git commit -m "Describe the update"

# 현재 브랜치를 원격 Git에 반영
git push origin HEAD
```

## NAS/Synology 배포

Git 반영 뒤 NAS SSH에서 실행합니다.

```bash
cd /volume4/llm_lore_create/llm_test
git pull --ff-only
docker compose up -d --build
docker compose ps
```

Python 패키지, Dockerfile, Compose 파일이 바뀐 업데이트는 반드시 `--build`를 포함합니다.

## 절대 Git에 올리지 않을 항목

다음은 API 키 또는 실제 사용자 데이터가 포함될 수 있으므로 커밋하지 않습니다.

- `.env`
- `data/`
- `storage/`
- SQLite DB 파일 (`*.db`, `*.sqlite3`)

커밋 전에는 항상 확인합니다.

```powershell
git status
```

예상하지 못한 파일이 보이면 `git add .`를 사용하지 말고, 필요한 파일만 명시적으로 `git add` 하세요.

## 2026-08-06 명세서 전체 업데이트

현재 작업 브랜치와 권장 커밋 라인은 다음과 같습니다.

```text
브랜치: claude/worldbuilding-llm-system-fQCoJ
커밋: feat: add world metadata schema and novel mode
```

Windows PowerShell에서 실행합니다.

```powershell
cd "C:\Users\HongjunChoi\Downloads\llm_test-claude-worldbuilding-llm-system-fQCoJ\llm_test"
git status
git branch --show-current
git add .gitignore app.py llm_client.py models.py simulation_engine.py `
  MUST_READ_UPDATES.md PIPELINE_GUIDE.md WORLDLLM_UPDATE_SPEC.md `
  templates/base.html templates/index.html templates/metadata.html templates/novel.html `
  tests/test_update_spec.py
git diff --cached --check
git commit -m "feat: add world metadata schema and novel mode"
git push origin claude/worldbuilding-llm-system-fQCoJ
```

Synology SSH에서 갱신합니다. 이전 화면의 `Bind mount failed` 오류를 막기 위해 볼륨 폴더를 먼저 만듭니다.

```bash
cd /volume4/llm_lore_create/llm_test
git pull --ff-only origin claude/worldbuilding-llm-system-fQCoJ
mkdir -p data storage
docker compose down
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 worldllm
```

기존 `storage/*.png` 중 이미 Git이 추적 중인 파일은 `.gitignore`를 추가해도 자동으로 추적 해제되지 않습니다. 실제 이미지가 저장소에 올라가면 안 되는 경우 별도 백업 후 `git rm --cached` 여부를 사용자가 결정해야 합니다.
