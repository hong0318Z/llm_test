"""
LLM Client - GitHub Models API (OpenAI 호환) 또는 Anthropic API 지원
GitHub Models: https://models.inference.ai.azure.com
  - GITHUB_TOKEN 환경변수 필요
  - 모델: claude-3-5-sonnet (GitHub Models에서 제공하는 모델명 사용)
Anthropic API:
  - ANTHROPIC_API_KEY 환경변수 필요
"""
import os
import json
from openai import OpenAI


def get_llm_client():
    """환경변수에 따라 클라이언트 반환"""
    github_token = os.environ.get("GITHUB_TOKEN")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    if github_token:
        return OpenAI(
            base_url="https://models.inference.ai.azure.com",
            api_key=github_token,
        ), os.environ.get("LLM_MODEL", "claude-3-5-sonnet")
    elif anthropic_key:
        # Anthropic도 OpenAI 호환 엔드포인트 제공
        return OpenAI(
            base_url="https://api.anthropic.com/v1/",
            api_key=anthropic_key,
        ), os.environ.get("LLM_MODEL", "claude-3-5-sonnet-20241022")
    else:
        raise EnvironmentError(
            "GITHUB_TOKEN 또는 ANTHROPIC_API_KEY 환경변수가 필요합니다."
        )


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
            lines.append(f"    {item['content'][:300]}")

    return "\n".join(lines)


def run_tick(config: dict, tick_number: int, entries: list) -> dict:
    """
    단일 틱 실행. LLM을 호출해 세계관 변화를 반환.
    반환값: {reasoning, events, entry_updates, new_entries, deactivated_entries}
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
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    result = json.loads(raw)
    result["_raw"] = raw
    return result
