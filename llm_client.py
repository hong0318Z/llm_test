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
from flask import session, has_request_context
import threading

_connection_context = threading.local()

def set_active_user(user_id):
    """백그라운드 시뮬레이션 스레드가 실행자 개인 설정을 사용하도록 지정."""
    _connection_context.user_id = user_id

def _user_connection_settings():
    try:
        from models import UserLlmSettings
        uid = session.get("user_id") if has_request_context() else getattr(_connection_context, "user_id", None)
        return UserLlmSettings.get_for_user(uid) if uid else None
    except Exception:
        return None

COPILOT_BASE_URL = "https://api.githubcopilot.com"
DEFAULT_MODEL = "claude-sonnet-4.5"
MAX_TOKENS = 16000  # Copilot API 최대값

# 세계관 직렬화가 이 토큰 수를 넘으면 자동 요약 실행
CONTEXT_SUMMARY_THRESHOLD = 120_000


def get_llm_client(model_override: str = None):

    """LLM API 클라이언트 반환.

    LLM_BASE_URL 환경변수가 설정되어 있으면 (예: mlx_lm.server 같은 로컬
    OpenAI 호환 서버) 해당 엔드포인트를 사용하고, 없으면 GitHub Copilot
    API를 사용합니다.
    """
    local_base_url = os.environ.get("LLM_BASE_URL")
    if local_base_url:
        model = model_override or os.environ.get("LLM_MODEL", "local-model")
        client = OpenAI(
            base_url=local_base_url,
            api_key=os.environ.get("LLM_API_KEY", "not-needed"),
        )
        return client, model

    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise EnvironmentError(
            "GITHUB_TOKEN 환경변수가 필요합니다.\n"
            ".env 파일에 GITHUB_TOKEN=your_token 을 추가하세요.\n"
            "또는 로컬 모델을 사용하려면 LLM_BASE_URL을 설정하세요 (예: http://localhost:8080/v1)."
        )

    """Copilot 또는 OpenAI 호환 로컬/원격 서버 클라이언트 반환.

    LLM_BASE_URL을 지정하면 Ollama, LM Studio, vLLM 등으로 전환된다.
    """
    settings = _user_connection_settings()
    ui_base_url = settings.llm_base_url if settings else ""
    ui_api_key = settings.llm_api_key if settings else ""
    base_url = ui_base_url or os.environ.get("LLM_BASE_URL") or COPILOT_BASE_URL
    api_key = ui_api_key or os.environ.get("LLM_API_KEY") or os.environ.get("GITHUB_TOKEN")
    if not api_key:
        # Ollama 등 인증 없는 OpenAI 호환 서버도 OpenAI SDK에는 더미 키가 필요하다.
        if os.environ.get("LLM_BASE_URL"):
            api_key = "local-no-key"
        else:
            raise EnvironmentError("GITHUB_TOKEN 또는 LLM_API_KEY가 필요합니다.")

    model = model_override or (settings.llm_model if settings and settings.llm_model else None) or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        default_headers=({
            "Editor-Version": "vscode/1.95.0",
            "Editor-Plugin-Version": "copilot-chat/0.22.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Openai-Organization": "github-copilot",
        } if base_url == COPILOT_BASE_URL else {}),
    )
    return client, model


def get_embedding_client():
    """임베딩 전용 OpenAI 호환 endpoint. 미지정 시 LLM endpoint를 재사용한다."""
    settings = _user_connection_settings()
    ui_base_url = settings.embedding_base_url if settings else ""
    ui_api_key = settings.embedding_api_key if settings else ""
    base_url = ui_base_url or os.environ.get("EMBEDDING_BASE_URL") or os.environ.get("LLM_BASE_URL") or COPILOT_BASE_URL
    api_key = ui_api_key or os.environ.get("EMBEDDING_API_KEY") or os.environ.get("LLM_API_KEY") or os.environ.get("GITHUB_TOKEN") or "local-no-key"
    return OpenAI(base_url=base_url, api_key=api_key)


def generate_embedding(text: str, model: str) -> list:
    response = get_embedding_client().embeddings.create(model=model, input=text)
    return response.data[0].embedding


def generate_auto_tags(title: str, category: str, content: str) -> list:
    """벡터 검색 보조용 의미 태그. 실패해도 호출자는 기존 키워드를 계속 사용한다."""
    client, model = get_llm_client()
    prompt = ("세계관 검색용 태그를 5~10개 JSON 배열로만 반환하세요. "
              "고유명사, 관계, 장소, 주제, 시대를 포함하세요.\n"
              f"제목:{title}\n분류:{category}\n내용:{content[:1200]}")
    raw = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], temperature=0.1, max_tokens=160).choices[0].message.content or "[]"
    parsed = _parse_json_safe(raw, "auto_tags")
    tags = parsed if isinstance(parsed, list) else parsed.get("tags", [])
    return [str(tag).strip() for tag in tags if str(tag).strip()][:10]


def cosine_similarity(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b): return -1.0
    import math
    den = math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / den if den else -1.0


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

위 세계관에서 이번 틱에 발생할 사건과 변화를 JSON으로 생성하세요.{story_beat_section}

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

WORLD_DESIGN_PROMPT = """당신은 세계관 설계자입니다. 사용자의 큰 맥락을 분석해 세계관 DB 초안을 설계하세요.
필요한 정보가 애매하면 먼저 질문하고, 답변이 충분하면 세력·인물·장소·사건·규칙/법·마법/기술 등 적절한 항목을 만드세요.
이미 정해진 사실은 바꾸지 마세요. 없는 내용을 사실처럼 과도하게 단정하지 마세요.

반드시 아래 JSON만 출력하세요.
{
  "summary": "이해한 세계관 요약",
  "questions": ["추가로 결정하면 좋은 질문"],
  "entries": [{"title": "이름", "category": "카테고리", "content": "DB에 저장할 구체적 설명", "references": ["연관 항목 제목"]}],
  "ready": true
}

questions는 정말 중요한 미결정 사항만 최대 5개, entries는 3~12개로 작성하세요. 답변이 부족하면 questions를 채우고 ready를 false로 하세요.
"""


def design_world(context: str, answers: str = "", existing_entries: list = None) -> dict:
    """큰 맥락을 DB 엔트리 초안과 보완 질문으로 변환한다. 저장은 호출자가 승인 후 수행한다."""
    client, model = get_llm_client()
    existing = serialize_world_state(existing_entries or [], max_chars=250) if existing_entries else "(아직 없음)"
    prompt = f"=== 사용자의 큰 맥락 ===\n{context}\n\n=== 보완 답변 ===\n{answers or '(없음)'}\n\n=== 기존 DB (중복 생성 금지) ===\n{existing}"
    response = client.chat.completions.create(model=model, messages=[
        {"role": "system", "content": WORLD_DESIGN_PROMPT}, {"role": "user", "content": prompt}
    ], temperature=0.55, max_tokens=3000)
    raw = response.choices[0].message.content or ""
    result = _parse_json_safe(raw, "world_design")
    result["_raw"] = raw
    return result


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
            version_tag = f" [{item['version_note']}]" if item.get("version_note") else ""
            lines.append(f"  ID={item['id']} | {item['title']}{creator_tag}{version_tag}{ref_str}")
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


def build_story_beat_section(story_beats: list, tick_number: int) -> str:
    """현재 틱의 스토리 비트 가이드 섹션을 생성"""
    if not story_beats:
        return ""

    # 현재 틱 비트
    current = [b for b in story_beats if b["tick_number"] == tick_number]
    # 앞으로 남은 비트 (다음 3개까지)
    future = sorted([b for b in story_beats if b["tick_number"] > tick_number], key=lambda x: x["tick_number"])[:3]
    # 이미 지난 비트 (직전 1개)
    past = sorted([b for b in story_beats if b["tick_number"] < tick_number], key=lambda x: x["tick_number"])
    past = past[-1:] if past else []

    lines = ["\n\n=== 스토리 아크 가이드 ==="]

    narrative_goal = story_beats[0].get("_narrative_goal", "") if story_beats else ""
    if narrative_goal:
        lines.append(f"[전체 서사 목표] {narrative_goal}")

    if past:
        b = past[0]
        label = f"[{b['beat_label']}] " if b.get("beat_label") else ""
        lines.append(f"\n▶ 직전 비트 (틱 {b['tick_number']}): {label}{b['title']}")

    if current:
        lines.append("\n🎯 이번 틱 목표 (반드시 반영하세요):")
        for b in current:
            label = f"[{b['beat_label']}] " if b.get("beat_label") else ""
            fixed_mark = "📌 " if b.get("is_fixed") else "✨ AI 자유 해석: "
            lines.append(f"  {fixed_mark}{label}{b['title']}")
            if b.get("description"):
                lines.append(f"    → {b['description']}")
    else:
        lines.append("\n(이번 틱 지정 비트 없음 - 전체 서사 흐름에 맞게 자유롭게 진행)")

    if future:
        lines.append("\n⏭ 향후 예정 비트 (장기 복선 고려):")
        for b in future:
            label = f"[{b['beat_label']}] " if b.get("beat_label") else ""
            lines.append(f"  틱 {b['tick_number']}: {label}{b['title']}")

    lines.append("\n이번 틱의 사건은 위 아크 가이드를 따르거나 자연스럽게 연결되어야 합니다.")
    return "\n".join(lines)


def run_tick(config: dict, tick_number: int, entries: list, recent_context: str = "",
             story_beats: list = None, prompt_overrides: dict = None,
             model_override: str = None) -> dict:
    """단일 틱 실행. LLM을 호출해 세계관 변화를 반환."""
    client, model = get_llm_client(model_override)
    max_chars = config.get("max_content_chars") or 500
    rag_budget = config.get("rag_token_budget") or 0
    overrides = prompt_overrides or {}

    # RAG: 세계관이 예산의 50%를 초과하면 관련 엔트리만 선택
    rag_info = None
    if rag_budget > 0:
        full_state = serialize_world_state(entries, max_chars=max_chars)
        if estimate_tokens(full_state) > rag_budget // 2:
            entries, rag_info = select_entries_rag(entries, recent_context, rag_budget, max_chars)

    sys_tmpl = overrides.get("simulation_system", SYSTEM_PROMPT_TEMPLATE)
    user_tmpl = overrides.get("simulation_user", USER_PROMPT_TEMPLATE)

    system_prompt = sys_tmpl.format(
        prompt_level_1=config.get("prompt_level_1") or "없음",
        prompt_level_2=config.get("prompt_level_2") or "없음",
        prompt_level_3=config.get("prompt_level_3") or "없음",
    )
    world_state = serialize_world_state(entries, max_chars=max_chars)
    story_beat_section = build_story_beat_section(story_beats or [], tick_number)
    user_prompt = user_tmpl.format(
        tick_number=tick_number,
        world_state=world_state,
        story_beat_section=story_beat_section,
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


def run_summary(config: dict, tick_number: int, entries: list, prompt_overrides: dict = None,
                model_override: str = None) -> dict:
    """컨텍스트 한계 근접 시 전체 세계관을 압축 요약."""
    client, model = get_llm_client(model_override)
    max_chars = config.get("max_content_chars") or 500
    overrides = prompt_overrides or {}

    world_state = serialize_world_state(entries, max_chars=max_chars)
    tmpl = overrides.get("summary", SUMMARY_PROMPT_TEMPLATE)
    user_prompt = tmpl.format(
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


TIMELINE_GEN_PROMPT = """\
당신은 세계관 타임라인 작가입니다.
주어진 엔트리를 중심으로 해당 엔트리의 서사 타임라인을 생성하세요.

=== 대상 엔트리 ===
[{category}] {title}
{content}

=== 세계관 컨텍스트 ===
{world_state}

=== 추가 지시 ===
{extra_prompt}

위 엔트리를 중심으로 흥미로운 타임라인(서사 흐름)을 생성하세요.
에피소드는 시간 순서대로 작성하며, 각 에피소드는 구체적인 사건을 담으세요.

출력 JSON 스키마:
{{
  "timeline_name": "타임라인 이름 (간결하게)",
  "timeline_description": "이 타임라인의 전체적인 설명",
  "episodes": [
    {{
      "tick_number": 1,
      "title": "에피소드 제목",
      "description": "에피소드 상세 내용 (2-4문장)"
    }}
  ]
}}

반드시 유효한 JSON만 출력하세요 (마크다운 없이).
"""


def generate_timeline(entry: dict, world_entries: list, extra_prompt: str = "",
                      episode_count: int = 5, prompt_overrides: dict = None,
                      model_override: str = None) -> dict:
    """특정 엔트리를 중심으로 타임라인을 1회 LLM 호출로 생성"""
    client, model = get_llm_client(model_override)
    overrides = prompt_overrides or {}

    world_state = serialize_world_state(
        [e for e in world_entries if e["id"] != entry["id"]],
        max_chars=300,
    )

    tmpl = overrides.get("timeline_generate", TIMELINE_GEN_PROMPT)
    prompt = tmpl.format(
        category=entry.get("category", ""),
        title=entry.get("title", ""),
        content=entry.get("content", ""),
        world_state=world_state[:4000] if len(world_state) > 4000 else world_state,
        extra_prompt=extra_prompt or f"이 엔트리의 주요 사건을 {episode_count}개의 에피소드로 구성하세요.",
    )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=MAX_TOKENS,
    )

    raw = response.choices[0].message.content or ""
    result = _parse_json_safe(raw, "generate_timeline")
    result["_raw"] = raw
    result["_tokens_in"] = getattr(response.usage, "prompt_tokens", 0)
    result["_tokens_out"] = getattr(response.usage, "completion_tokens", 0)
    return result


# ── NAI 프롬프트 자동생성 ────────────────────────────────────

NAI_PROMPT_GENERATOR_TEMPLATE = """당신은 NovelAI(NAI) Diffusion 이미지 생성 전문 프롬프터입니다.
아래 세계관 엔트리 정보를 분석하여 NAI 이미지 생성 프롬프트를 작성하세요.

[엔트리 정보]
분류: {category}
이름: {title}
내용: {content}
키워드: {keywords}

[프롬프트 생성 절대 규칙]
1. 단부루(Danbooru) 및 노벨AI(NAI) 태그 규격을 따른다
2. 불필요한 감성적 묘사나 중복 태그는 철저히 배제하고 명시적인 키워드만 쉼표(,)로 구분하여 나열한다
3. 태그 그룹은 중괄호 {{ }}로 묶어 우선순위와 속성을 분리한다 (예: {{{{masterpiece}}}}, {{{{1girl}}}})
4. {{{{masterpiece}}}}, {{{{best quality}}}}는 반드시 포함
5. 영어 태그만 사용
6. 50개 이내로 간결하게

[결과 형식]
프롬프트 태그 문자열만 출력. 설명이나 주석 없이 순수 태그 목록만."""


def generate_nai_prompt(entry: dict, world_entries: list = None,
                        prompt_template: str = None, base_positive: str = "",
                        model_override: str = None) -> str:
    """엔트리 정보로 NAI 이미지 프롬프트 자동생성"""
    client, model = get_llm_client(model_override)
    tmpl = prompt_template or NAI_PROMPT_GENERATOR_TEMPLATE

    content = entry.get("content", "")
    if len(content) > 600:
        content = content[:600] + "..."

    user_prompt = tmpl.format(
        category=entry.get("category", ""),
        title=entry.get("title", ""),
        content=content,
        keywords=entry.get("keywords", ""),
    )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.7,
        max_tokens=512,
    )

    tags = (response.choices[0].message.content or "").strip()

    # 기본 긍정 태그가 있으면 앞에 붙임
    if base_positive:
        tags = base_positive.rstrip(", ") + ", " + tags

    return tags
