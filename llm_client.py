"""
LLM Client - GitHub Copilot API (OpenAI 호환)
엔드포인트: https://api.githubcopilot.com
  - GITHUB_TOKEN 환경변수에 GitHub Personal Access Token 설정
  - 모델: claude-sonnet-4.5 (context window: 200k tokens)
"""
import os
import re
import json
from openai import OpenAI

COPILOT_BASE_URL = "https://api.githubcopilot.com"
DEFAULT_MODEL = "claude-sonnet-4.5"
MAX_TOKENS = 16000  # Copilot API 최대값

# 세계관 직렬화가 이 토큰 수를 넘으면 자동 요약 실행
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


def _parse_json_safe(raw: str, context: str = "") -> dict:
    """
    LLM 응답을 JSON으로 파싱. 잘리거나 깨진 경우 빈 결과 반환.
    """
    clean = raw.strip()
    # 마크다운 코드블록 제거
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:]).rstrip("`").strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        # 응답이 잘린 경우: 마지막 완전한 JSON 객체를 닫아서 재시도
        try:
            # 열린 괄호 수만큼 닫기
            opens = clean.count("{") - clean.count("}")
            if opens > 0:
                patched = clean + ("}" * opens)
                return json.loads(patched)
        except Exception:
            pass
        # 최후 fallback: 빈 결과
        return {
            "_parse_error": True,
            "_raw_error": f"JSON 파싱 실패({context}): 응답이 잘렸거나 형식이 맞지 않습니다.",
            "reasoning": f"[파싱 오류] {context} - max_tokens 초과 또는 응답 형식 오류",
            "events": [],
            "entry_updates": [],
            "new_entries": [],
            "deactivated_entries": [],
        }


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
- 각 항목의 내용은 간결하게 작성하세요 (토큰 절약)

핵심 제약:
- [🔒유저] 태그가 붙은 엔트리는 절대 entry_updates나 deactivated_entries에 포함하지 마세요.
  이 엔트리들은 사용자가 설정한 세계관 코어이며 수정/비활성화가 금지됩니다.
- 유저 엔트리에서 파생된 변화를 표현해야 한다면, 반드시 new_entries로 새 엔트리를 생성하고
  references 필드에 원본 유저 엔트리 ID를 포함하세요.
"""

USER_PROMPT_TEMPLATE = """\
현재 틱: {tick_number}

=== 현재 세계관 상태 ===
{world_state}

위 세계관에서 이번 틱에 발생할 사건과 변화를 JSON으로 생성하세요.

출력 JSON 스키마:
{{
  "reasoning": "이번 틱의 전반적인 흐름 설명 (한국어, 3문장 이내)",
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

TRANSLATE_PROMPT_TEMPLATE = """\
아래 세계관 엔트리들을 영어로 번역하세요.
제목과 내용 모두 자연스러운 영어로 번역하되, 고유명사는 원문을 병기하세요.

=== 번역할 엔트리 ===
{entries_text}

출력 JSON 스키마:
{{
  "translations": [
    {{
      "id": 1,
      "title_en": "English Title",
      "content_en": "English content..."
    }}
  ]
}}
"""


def serialize_world_state(entries: list, max_chars: int = 500) -> str:
    """
    세계관 엔트리 목록을 LLM이 읽기 좋은 형태로 직렬화.
    max_chars: 엔트리당 내용 최대 글자 수 (0 = 제한 없음)
    """
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
            content = item["content"]
            if max_chars and max_chars > 0 and len(content) > max_chars:
                content = content[:max_chars] + f"…(+{len(item['content'])-max_chars}자 생략)"
            creator_tag = " [🔒유저]" if item.get("created_by") == "user" else ""
            lines.append(f"  ID={item['id']} | {item['title']}{creator_tag}{ref_str}")
            lines.append(f"    {content}")

    return "\n".join(lines)


def estimate_world_tokens(entries: list, config: dict = None) -> dict:
    """현재 세계관 + 프롬프트의 예상 토큰 수 반환"""
    max_chars = (config.get("max_content_chars") or 500) if config else 500
    world_state = serialize_world_state(entries, max_chars=max_chars)
    world_tokens = estimate_tokens(world_state)

    prompt_tokens = 0
    if config:
        system = SYSTEM_PROMPT_TEMPLATE.format(
            prompt_level_1=config.get("prompt_level_1") or "없음",
            prompt_level_2=config.get("prompt_level_2") or "없음",
            prompt_level_3=config.get("prompt_level_3") or "없음",
        )
        prompt_tokens = estimate_tokens(system) + estimate_tokens(USER_PROMPT_TEMPLATE)

    total = world_tokens + prompt_tokens
    return {
        "world_tokens": world_tokens,
        "prompt_tokens": prompt_tokens,
        "total_estimated": total,
        "context_limit": 200_000,
        "usage_pct": round(total / 200_000 * 100, 1),
        "entry_count": len(entries),
    }


def run_tick(config: dict, tick_number: int, entries: list, recent_context: str = "") -> dict:
    """단일 틱 실행. LLM을 호출해 세계관 변화를 반환."""
    client, model = get_llm_client()
    max_chars = config.get("max_content_chars") or 500
    rag_budget = config.get("rag_token_budget") or 0

    # RAG: 세계관이 예산의 50%를 초과하면 관련 엔트리만 선택
    rag_info = None
    if rag_budget > 0:
        full_state = serialize_world_state(entries, max_chars=max_chars)
        if estimate_tokens(full_state) > rag_budget // 2:
            entries, rag_info = select_entries_rag(entries, recent_context, rag_budget, max_chars)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        prompt_level_1=config.get("prompt_level_1") or "없음",
        prompt_level_2=config.get("prompt_level_2") or "없음",
        prompt_level_3=config.get("prompt_level_3") or "없음",
    )
    world_state = serialize_world_state(entries, max_chars=max_chars)
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
        max_tokens=MAX_TOKENS,
    )

    raw = response.choices[0].message.content or ""
    finish_reason = response.choices[0].finish_reason

    result = _parse_json_safe(raw, context=f"틱{tick_number}")
    if finish_reason == "length":
        result["_truncated"] = True
        result["reasoning"] = "[응답 잘림] " + result.get("reasoning", "")

    usage = response.usage
    result["_raw"] = raw
    result["_tokens_in"] = usage.prompt_tokens if usage else estimate_tokens(system_prompt + user_prompt)
    result["_tokens_out"] = usage.completion_tokens if usage else estimate_tokens(raw)
    result["_total_tokens"] = usage.total_tokens if usage else (result["_tokens_in"] + result["_tokens_out"])
    result["_rag_info"] = rag_info
    return result


def run_summary(config: dict, tick_number: int, entries: list) -> dict:
    """컨텍스트 한계 근접 시 전체 세계관을 압축 요약."""
    client, model = get_llm_client()
    max_chars = config.get("max_content_chars") or 500

    world_state = serialize_world_state(entries, max_chars=max_chars)
    user_prompt = SUMMARY_PROMPT_TEMPLATE.format(
        world_state=world_state,
        tick_number=tick_number,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.3,
        max_tokens=MAX_TOKENS,
    )

    raw = response.choices[0].message.content or ""
    result = _parse_json_safe(raw, context="요약")

    usage = response.usage
    result["_raw"] = raw
    result["_tokens_in"] = usage.prompt_tokens if usage else estimate_tokens(user_prompt)
    result["_tokens_out"] = usage.completion_tokens if usage else estimate_tokens(raw)
    result["_total_tokens"] = usage.total_tokens if usage else (result["_tokens_in"] + result["_tokens_out"])
    return result


def translate_entries(entries: list) -> dict:
    """선택된 엔트리들을 영문으로 번역."""
    client, model = get_llm_client()

    entries_text = "\n".join([
        f"ID={e['id']} [{e['category']}] {e['title']}\n  {e['content'][:400]}"
        for e in entries
    ])
    user_prompt = TRANSLATE_PROMPT_TEMPLATE.format(entries_text=entries_text)

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.3,
        max_tokens=MAX_TOKENS,
    )

    raw = response.choices[0].message.content or ""
    result = _parse_json_safe(raw, context="번역")

    usage = response.usage
    result["_tokens_in"] = usage.prompt_tokens if usage else estimate_tokens(user_prompt)
    result["_tokens_out"] = usage.completion_tokens if usage else estimate_tokens(raw)
    result["_total_tokens"] = usage.total_tokens if usage else (result["_tokens_in"] + result["_tokens_out"])
    return result


def needs_summary(entries: list, max_chars: int = 500) -> bool:
    """현재 세계관이 컨텍스트 한계에 근접했는지 확인"""
    world_state = serialize_world_state(entries, max_chars=max_chars)
    return estimate_tokens(world_state) >= CONTEXT_SUMMARY_THRESHOLD


def generate_keywords(title: str, category: str, content: str) -> list:
    """엔트리 내용에서 핵심 키워드 5개를 LLM으로 추출"""
    client, model = get_llm_client()
    prompt = (
        f"세계관 엔트리에서 핵심 키워드 5개를 추출하세요.\n"
        f"키워드는 다른 엔트리와의 관련성을 찾는 데 쓰입니다.\n\n"
        f"제목: {title}\n분류: {category}\n내용: {content[:400]}\n\n"
        f'JSON으로만 응답: {{"keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"]}}'
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        parsed = _parse_json_safe(raw, "generate_keywords")
        kws = parsed.get("keywords", [])
        return [str(k).strip() for k in kws if k][:5]
    except Exception:
        return []


def _extract_context_words(text: str) -> set:
    """텍스트에서 의미있는 단어 집합 추출 (한국어 2자+, 영어 3자+)"""
    words = re.findall(r'[가-힣]{2,}|[a-zA-Z]{3,}', text)
    return {w.lower() for w in words}


def _score_entry_relevance(entry: dict, context_words: set) -> float:
    """최근 컨텍스트와 엔트리의 관련도 점수 계산"""
    score = 0.0
    # 키워드 매칭: 엔트리 키워드가 컨텍스트 단어에 포함되면 가산
    kw_str = entry.get("keywords") or ""
    for kw in [k.strip().lower() for k in kw_str.split(",") if k.strip()]:
        if kw in context_words or any(kw in cw for cw in context_words):
            score += 2.0
    # 제목 단어가 컨텍스트에 언급된 경우
    title_words = _extract_context_words(entry.get("title", ""))
    score += len(title_words & context_words) * 3.0
    # 최신성 보너스 (최근 틱에 생성될수록 관련 가능성 높음)
    score += (entry.get("tick_created") or 0) * 0.05
    return score


def select_entries_rag(
    all_entries: list,
    recent_context: str,
    token_budget: int,
    max_chars: int = 500,
) -> tuple:
    """
    RAG 기반 컨텍스트 엔트리 선택.
    - 유저 엔트리: 항상 포함 (세계관 코어)
    - LLM 엔트리: 관련도 점수 순으로 토큰 예산 내에서 선택
    Returns: (selected_entries, rag_stats)
    """
    context_words = _extract_context_words(recent_context)

    user_entries = [e for e in all_entries if e.get("created_by") == "user"]
    llm_entries = [e for e in all_entries if e.get("created_by") != "user"]

    # 유저 엔트리 토큰 소비량 계산
    used_tokens = estimate_tokens(serialize_world_state(user_entries, max_chars=max_chars))
    remaining = token_budget - used_tokens

    # LLM 엔트리를 관련도 순으로 정렬하여 예산 내에서 선택
    scored = sorted(
        [(e, _score_entry_relevance(e, context_words)) for e in llm_entries],
        key=lambda x: x[1],
        reverse=True,
    )

    selected_llm = []
    for entry, _score in scored:
        entry_text = f"  ID={entry['id']} | {entry.get('title', '')}\n    {(entry.get('content') or '')[:max_chars]}\n"
        entry_tokens = estimate_tokens(entry_text)
        if remaining >= entry_tokens:
            selected_llm.append(entry)
            remaining -= entry_tokens
        if remaining <= 50:
            break

    selected = user_entries + selected_llm
    return selected, {
        "rag_applied": True,
        "total": len(all_entries),
        "selected": len(selected),
        "skipped": len(all_entries) - len(selected),
        "user_entries": len(user_entries),
        "llm_selected": len(selected_llm),
        "llm_total": len(llm_entries),
    }


def generate_entry(title: str, category: str, hint: str, ref_entries: list) -> dict:
    """유저 입력(제목·분류·힌트·참조)을 기반으로 엔트리 내용을 LLM이 생성"""
    client, model = get_llm_client()

    ref_block = ""
    if ref_entries:
        lines = []
        for e in ref_entries:
            lines.append(f"  [{e['category']}] {e['title']}: {e['content'][:300]}")
        ref_block = "\n참조 엔트리:\n" + "\n".join(lines)

    hint_block = f"\n사용자 힌트/초안:\n{hint}" if hint else ""

    prompt = f"""다음 정보를 바탕으로 세계관 엔트리의 상세 내용을 작성해주세요.

제목: {title}
분류: {category}{hint_block}{ref_block}

요구 사항:
- 세계관 설정에 어울리는 구체적이고 풍부한 묘사
- 참조 엔트리와 자연스럽게 연결되는 내용
- 마크다운 기호(**볼드**, # 헤더 등) 사용 금지, 일반 텍스트만
- 한국어로 작성
- 반드시 JSON으로만 응답: {{"content": "생성된 내용"}}"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    raw = response.choices[0].message.content.strip()
    parsed = _parse_json_safe(raw, "generate_entry")
    content = parsed.get("content", raw)
    tokens_in = getattr(response.usage, "prompt_tokens", 0)
    tokens_out = getattr(response.usage, "completion_tokens", 0)
    return {
        "content": content,
        "_tokens_in": tokens_in,
        "_tokens_out": tokens_out,
    }
