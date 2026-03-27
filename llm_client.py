"""
LLM Client - GitHub Copilot API (OpenAI 호환)
엔드포인트: https://api.githubcopilot.com
  - GITHUB_TOKEN 환경변수에 GitHub Personal Access Token 설정
  - 모델: claude-sonnet-4.5 (context window: 200k tokens)
"""
import os
import json
from openai import OpenAI

COPILOT_BASE_URL = "https://api.githubcopilot.com"
DEFAULT_MODEL = "claude-sonnet-4.5"

# 세계관 직렬화가 이 토큰 수를 넘으면 자동 요약 실행
# (claude-sonnet-4.5 context 200k, 프롬프트/응답 여유분 제외)
CONTEXT_SUMMARY_THRESHOLD = 120_000


def get_llm_client():
    """GitHub Copilot API 클라이언트 반환"""
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise EnvironmentError(
            "GITHUB_TOKEN 환경변수가 필요합니다.\n"
            ".env 파일에 GITHUB_TOKEN=your_token 을 추가하세요."
        )
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    client = OpenAI(
        base_url=COPILOT_BASE_URL,
        api_key=github_token,
        default_headers={
            "Editor-Version": "vscode/1.95.0",
            "Editor-Plugin-Version": "copilot-chat/0.22.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Openai-Organization": "github-copilot",
        },
    )
    return client, model


def estimate_tokens(text: str) -> int:
    """한국어 포함 텍스트의 토큰 수 추정 (1토큰 ≈ 1.5자 기준)"""
    return max(1, int(len(text) / 1.5))


SYSTEM_PROMPT_TEMPLATE = """\
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
"""

USER_PROMPT_TEMPLATE = """\
현재 틱: {tick_number}

=== 현재 세계관 상태 ===
{world_state}

위 세계관에서 이번 틱에 발생할 사건과 변화를 JSON으로 생성하세요.

출력 JSON 스키마:
{{
  "reasoning": "이번 틱의 전반적인 흐름 설명 (한국어)",
  "events": [
    {{
      "type": "world_event",
      "description": "사건 설명",
      "affected_entry_ids": [1, 2]
    }}
  ],
  "entry_updates": [
    {{
      "id": 1,
      "new_content": "업데이트된 내용",
      "reason": "변경 이유"
    }}
  ],
  "new_entries": [
    {{
      "title": "새 인물/개념/사물 이름",
      "category": "인물",
      "content": "내용",
      "references": [1, 2]
    }}
  ],
  "deactivated_entries": [
    {{
      "id": 3,
      "reason": "소멸/사망/소실 이유"
    }}
  ]
}}
"""

SUMMARY_PROMPT_TEMPLATE = """\
다음은 현재까지 축적된 세계관 엔트리 전체입니다.
컨텍스트 한계에 근접했으므로, 이 세계관을 카테고리별로 압축 요약해주세요.

=== 전체 세계관 ===
{world_state}

출력 JSON 스키마:
{{
  "reasoning": "요약 수행 이유 및 요약 방침",
  "summaries": [
    {{
      "category": "카테고리명",
      "title": "요약 제목 (예: '세력 전체 요약 - 틱 {tick_number}')",
      "content": "해당 카테고리의 모든 엔트리를 포함한 압축 요약",
      "covered_entry_ids": [1, 2, 3]
    }}
  ]
}}
"""


def serialize_world_state(entries: list) -> str:
    """세계관 엔트리 목록을 LLM이 읽기 좋은 형태로 직렬화"""
    lines = []
    by_category = {}
    for e in entries:
        cat = e.get("category", "기타")
        by_category.setdefault(cat, []).append(e)

    for cat, items in by_category.items():
        lines.append(f"\n[{cat}]")
        for item in items:
            ref_ids = item.get("references", [])
            ref_str = f" (참조: {ref_ids})" if ref_ids else ""
            lines.append(f"  ID={item['id']} | {item['title']}{ref_str}")
            lines.append(f"    {item['content'][:500]}")

    return "\n".join(lines)


def run_tick(config: dict, tick_number: int, entries: list) -> dict:
    """
    단일 틱 실행. LLM을 호출해 세계관 변화를 반환.
    반환값에 _raw, _tokens_in, _tokens_out, _total_tokens 포함
    """
    client, model = get_llm_client()

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        prompt_level_1=config.get("prompt_level_1") or "없음",
        prompt_level_2=config.get("prompt_level_2") or "없음",
        prompt_level_3=config.get("prompt_level_3") or "없음",
    )
    world_state = serialize_world_state(entries)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        tick_number=tick_number,
        world_state=world_state,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.8,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content
    # JSON 파싱 (코드블록 감싸진 경우 대응)
    clean = raw.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.split("\n")[1:])
        clean = clean.rstrip("`").strip()
    result = json.loads(clean)

    usage = response.usage
    result["_raw"] = raw
    result["_tokens_in"] = usage.prompt_tokens if usage else estimate_tokens(system_prompt + user_prompt)
    result["_tokens_out"] = usage.completion_tokens if usage else estimate_tokens(raw)
    result["_total_tokens"] = usage.total_tokens if usage else (result["_tokens_in"] + result["_tokens_out"])
    return result


def run_summary(config: dict, tick_number: int, entries: list) -> dict:
    """
    컨텍스트 한계 근접 시 전체 세계관을 압축 요약.
    반환값에 _tokens_in, _tokens_out, _total_tokens 포함
    """
    client, model = get_llm_client()

    world_state = serialize_world_state(entries)
    user_prompt = SUMMARY_PROMPT_TEMPLATE.format(
        world_state=world_state,
        tick_number=tick_number,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.3,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content
    clean = raw.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.split("\n")[1:])
        clean = clean.rstrip("`").strip()
    result = json.loads(clean)

    usage = response.usage
    result["_raw"] = raw
    result["_tokens_in"] = usage.prompt_tokens if usage else estimate_tokens(user_prompt)
    result["_tokens_out"] = usage.completion_tokens if usage else estimate_tokens(raw)
    result["_total_tokens"] = usage.total_tokens if usage else (result["_tokens_in"] + result["_tokens_out"])
    return result


def needs_summary(entries: list) -> bool:
    """현재 세계관이 컨텍스트 한계에 근접했는지 확인"""
    world_state = serialize_world_state(entries)
    estimated = estimate_tokens(world_state)
    return estimated >= CONTEXT_SUMMARY_THRESHOLD
