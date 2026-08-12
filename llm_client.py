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
MAX_TOKENS = 262144  # 256K 출력 상한. 실제 생성 길이는 모델과 프롬프트가 결정한다.

# 세계관 직렬화가 이 토큰 수를 넘으면 자동 요약 실행
CONTEXT_SUMMARY_THRESHOLD = 240_000


def get_max_output_tokens():
    """현재 설정의 chat completion 출력 상한을 반환한다."""
    raw = os.environ.get("LLM_MAX_OUTPUT_TOKENS")
    if raw is None:
        try:
            from models import AppSettings
            raw = AppSettings.get().llm_max_output_tokens
        except Exception:
            raw = MAX_TOKENS
    try:
        return max(1, min(MAX_TOKENS, int(raw)))
    except (TypeError, ValueError):
        return MAX_TOKENS


def get_entry_char_limit():
    """마스터 설정의 엔트리별 글자 제한. 0은 무제한이다."""
    try:
        from models import AppSettings
        return max(0, int(AppSettings.get().max_llm_entry_chars or 0))
    except Exception:
        return 0


def get_llm_client(model_override: str = None):

    """LLM API 클라이언트 반환.

    LLM_BASE_URL 환경변수가 설정되어 있으면 (예: mlx_lm.server 같은 로컬
    OpenAI 호환 서버) 해당 엔드포인트를 사용하고, 없으면 GitHub Copilot
    API를 사용합니다.
    """
    settings = _user_connection_settings()
    ui_base_url = settings.llm_base_url if settings else ""
    ui_api_key = settings.llm_api_key if settings else ""
    base_url = ui_base_url or os.environ.get("LLM_BASE_URL") or COPILOT_BASE_URL
    api_key = ui_api_key or os.environ.get("LLM_API_KEY") or os.environ.get("GITHUB_TOKEN")
    if not api_key:
        # 로컬 OpenAI 호환 서버는 대개 인증이 없지만 SDK에는 더미 키가 필요하다.
        if base_url != COPILOT_BASE_URL: api_key = "local-no-key"
        else: raise EnvironmentError("GitHub Copilot에는 GITHUB_TOKEN이 필요합니다. 로컬 모델은 LLM Base URL을 설정하세요.")

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


def _extract_json_object(raw: str):
    """코드블록·설명문·이중 인코딩 문자열 안에서 완전한 JSON 객체를 찾는다."""
    text=str(raw or "").strip();candidates=[text]
    candidates.extend(re.findall(r"```(?:json)?\s*([\s\S]*?)```",text,flags=re.IGNORECASE))
    decoder=json.JSONDecoder();found=[]
    for candidate in candidates:
        clean=candidate.strip()
        try:
            value=json.loads(clean)
            if isinstance(value,dict):found.append(value)
        except (json.JSONDecodeError,TypeError):pass
        for match in re.finditer(r"\{",clean):
            try:
                value,_=decoder.raw_decode(clean[match.start():])
                if isinstance(value,dict):found.append(value)
            except json.JSONDecodeError:continue
    if not found:return None
    def score(value):
        keys=set(value)
        return (10 if keys & {"content","public_content","public","본문","entry"} else 0)+(4 if keys & {"secret","secret_content","비밀"} else 0)+(3 if keys & {"attributes","metadata","메타데이터"} else 0)+len(keys)/100
    return max(found,key=score)


def _normalize_generated_attributes(value):
    """LLM의 객체/배열형 능력치 응답을 저장 API의 축 이름 객체 형식으로 통일한다."""
    if isinstance(value,dict) and isinstance(value.get("attributes"),dict):value=value["attributes"]
    if isinstance(value,dict) and isinstance(value.get("능력치"),dict):value=value["능력치"]
    if isinstance(value,list):
        converted={}
        for item in value:
            if not isinstance(item,dict):continue
            name=item.get("axis_name") or item.get("name") or item.get("능력치")
            if name:converted[str(name)]={"value":item.get("value",item.get("score",item.get("수치"))),"description":item.get("description",item.get("reason",item.get("근거","")))}
        value=converted
    if not isinstance(value,dict):return {}
    result={}
    for name,item in value.items():
        if isinstance(item,dict):
            score=item.get("value",item.get("score",item.get("수치")))
            description=item.get("description",item.get("reason",item.get("근거","")))
            result[str(name)]={"value":score,"description":str(description or "")}
        else:result[str(name)]={"value":item,"description":""}
    return result


def _split_embedded_entry_metadata(text: str):
    """공개 본문 뒤에 붙은 secret/attributes JSON을 분리한다."""
    source=str(text or "");decoder=json.JSONDecoder();matches=[]
    for match in re.finditer(r"\{",source):
        try:value,length=decoder.raw_decode(source[match.start():])
        except json.JSONDecodeError:continue
        if isinstance(value,dict) and set(value) & {"secret","secret_content","비밀","attributes","metadata","메타데이터"}:
            matches.append((match.start(),match.start()+length,value))
    if not matches:return source,None
    start,end,value=max(matches,key=lambda item:(len(set(item[2]) & {"secret","secret_content","비밀","attributes","metadata","메타데이터"}),item[1]-item[0]))
    if set(value) & {"content","public_content","public","본문","body"}:
        cleaned=next((value.get(k) for k in ("content","public_content","public","본문","body") if value.get(k) is not None),"")
    else:cleaned=(source[:start]+source[end:]).replace("```json","").replace("```JSON","").replace("```","")
    return str(cleaned or "").strip(),value


def _normalize_generated_entry_response(raw: str, parsed) -> dict:
    """공개 본문, 비밀, 능력치를 분리하고 구조화 원문의 본문 유입을 차단한다."""
    payload=parsed if isinstance(parsed,dict) and not parsed.get("_parse_error") else _extract_json_object(raw)
    if not isinstance(payload,dict):
        text=str(raw or "").strip();looks_structured=text.startswith(("{","```")) or any(marker in text for marker in ('"secret"','"attributes"','"비밀"','"메타데이터"'))
        if looks_structured:raise ValueError("LLM이 비밀·능력치가 포함된 JSON을 깨진 형식으로 반환해 본문 저장을 중단했습니다. 다시 생성하세요.")
        return {"content":text,"secret":"","attributes":{}}
    for _ in range(3):
        if isinstance(payload.get("entry"),dict):payload=payload["entry"];continue
        content_value=next((payload.get(k) for k in ("content","public_content","public","본문","body") if payload.get(k) is not None),"")
        nested=content_value if isinstance(content_value,dict) else (_extract_json_object(content_value) if isinstance(content_value,str) and content_value.strip().startswith(("{","```")) else None)
        if isinstance(nested,dict) and set(nested) & {"content","public_content","public","본문","entry","secret","attributes","metadata","메타데이터"}:
            merged=dict(nested)
            for key in ("secret","secret_content","비밀","attributes","metadata","메타데이터"):
                if key not in merged and key in payload:merged[key]=payload[key]
            payload=merged;continue
        break
    metadata=next((payload.get(k) for k in ("metadata","메타데이터","meta") if isinstance(payload.get(k),dict)),None)
    if metadata is None:
        metadata_text=next((payload.get(k) for k in ("metadata","메타데이터","meta") if isinstance(payload.get(k),str)),"")
        metadata=_extract_json_object(metadata_text) or {}
    content=next((payload.get(k) for k in ("content","public_content","public","본문","body") if payload.get(k) is not None),metadata.get("content",""))
    if isinstance(content,(dict,list)):raise ValueError("LLM 공개 본문이 문자열이 아니라 JSON 객체로 반환되었습니다. 다시 생성하세요.")
    content=str(content or "").strip()
    content,embedded=_split_embedded_entry_metadata(content)
    if isinstance(embedded,dict):
        for key in ("secret","secret_content","비밀","attributes","metadata","메타데이터"):
            if (key not in payload or not payload.get(key)) and embedded.get(key) is not None:payload[key]=embedded[key]
        embedded_meta=next((embedded.get(k) for k in ("metadata","메타데이터","meta") if isinstance(embedded.get(k),dict)),{})
        for key,value in embedded_meta.items():metadata.setdefault(key,value)
    secret=next((payload.get(k) for k in ("secret","secret_content","비밀") if payload.get(k) is not None),metadata.get("secret",metadata.get("비밀","")))
    attributes=payload.get("attributes",metadata.get("attributes",metadata.get("능력치",{})))
    return {"content":content,"secret":str(secret or ""),"attributes":_normalize_generated_attributes(attributes)}


SYSTEM_PROMPT_TEMPLATE = """\
당신은 세계관 자율 진화 엔진입니다.
주어진 세계관 엔트리들을 기반으로 논리적으로 일관된 사건과 변화를 생성합니다.

TRPG 작성 규칙:
- [인물]은 D&D 스타일 캐릭터 시트로 작성: 정체성/종족·직업·레벨, 능력치(STR DEX CON INT WIS CHA), HP/AC, 배경·성향, 기술·장비, 목표·관계·약점.
- [인물] 본문은 정규식 뷰가 인식하도록 **정체성**, **종족/직업/레벨**, **능력치**, **HP/AC**, **배경·성향**, **기술·장비**, **목표·관계·약점**, **연도별 특기사항** 표기를 일관되게 사용하세요.
- [장소]는 TRPG 장소 시트로 작성: 유형·지형·규모, 분위기, 주요 구역, 세력/주민, 자원·위험, 비밀·훅, 접근 경로.
- [세력]은 목표·조직·자원·지도자·동맹/적대·현재 계획·약점을, [사건]은 발생 연도·원인·전개·결과·영향을 포함하세요.
- primary_year가 있는 엔트리는 그 연도에 벌어진 특기사항을 우선하며, 인물/장소/세력의 content에 "## 연도별 특기사항"으로 연도와 사건을 기록하세요.

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
- 이번 틱의 new_entries는 최대 {entries_per_tick}개입니다. 값이 0이면 꼭 필요한 수만 생성하세요.
- 엔트리 내용 길이: {entry_length_rule}
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
      "primary_year": "사건이 집중되는 연도 (없으면 빈 문자열)",
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


def design_world(context: str, answers: str = "", existing_entries: list = None,
                 prior_entries: list = None, batch_number: int = 1) -> dict:
    """큰 맥락을 DB 엔트리 초안과 보완 질문으로 변환한다. 저장은 호출자가 승인 후 수행한다."""
    client, model = get_llm_client()
    existing = serialize_world_state(existing_entries or [], max_chars=get_entry_char_limit()) if existing_entries else "(아직 없음)"
    prior = "\n".join(f"- [{e.get('category','')}] {e.get('title','')}" for e in (prior_entries or [])) or "(첫 번째 묶음)"
    prompt = f"=== 사용자의 큰 맥락 ===\n{context}\n\n=== 보완 답변 ===\n{answers or '(없음)'}\n\n=== 기존 DB (중복 생성 금지) ===\n{existing}\n\n=== 앞선 설계 묶음에서 이미 만든 항목 (절대 중복 금지) ===\n{prior}\n\n이번은 설계 묶음 {batch_number}입니다. 앞선 항목을 확장하는 서로 다른 6~12개 항목을 생성하세요. 관계·갈등·지리·제도 중 아직 비어 있는 영역을 우선하세요."
    response = client.chat.completions.create(model=model, messages=[
        {"role": "system", "content": WORLD_DESIGN_PROMPT}, {"role": "user", "content": prompt}
    ], temperature=0.55, max_tokens=get_max_output_tokens())
    raw = response.choices[0].message.content or ""
    result = _parse_json_safe(raw, "world_design")
    result["_raw"] = raw
    return result


WORLD_STRUCTURE_PROMPT = """당신은 세계관 데이터베이스 설계자입니다. 큰 맥락을 바탕으로 DB에 추가할 항목의 '설계 목록'만 만드세요.
본문을 길게 쓰지 말고, 이름·분류·핵심 속성·생성 이유를 명확히 제안하세요. 기존 항목과 같은 개념은 절대 제안하지 마세요.
반드시 JSON만 출력하세요:
{"summary":"설계 요약","questions":["정말 필요한 미결정 질문"],"items":[{"title":"항목명","category":"세력|인물|장소 등","attributes":["핵심 속성"],"purpose":"세계관에서 채우는 역할"}]}
"""


def plan_world_structure(context: str, target_count: int, answers: str, existing_entries: list, max_chars: int = None) -> dict:
    client, model = get_llm_client()
    max_chars=get_entry_char_limit() if max_chars is None else max(0,int(max_chars))
    existing = serialize_world_state(existing_entries, max_chars=max_chars) if existing_entries else "(없음)"
    prompt = f"=== 큰 맥락 ===\n{context}\n\n=== 보완 답변 ===\n{answers or '(없음)'}\n\n=== 기존 DB ===\n{existing}\n\n정확히 최대 {target_count}개 이하의 서로 다른 설계 항목을 제안하세요."
    raw = client.chat.completions.create(model=model, messages=[{"role":"system","content":WORLD_STRUCTURE_PROMPT},{"role":"user","content":prompt}], temperature=0.45, max_tokens=get_max_output_tokens()).choices[0].message.content or ""
    result = _parse_json_safe(raw, "world_structure")
    result["_raw"] = raw
    return result


WORLD_DETAIL_PROMPT = """당신은 세계관 DB 작성자입니다. 받은 설계 항목만 상세한 독립 DB 엔트리로 작성하세요.
{length_rule}
{metadata_rules}
Markdown으로 읽기 쉬운 TRPG 시트를 작성하세요.
- 인물: D&D 스타일(정체성, 종족/직업/레벨, STR·DEX·CON·INT·WIS·CHA, HP/AC, 배경·성향, 기술·장비, 목표·관계·약점, 연도별 특기사항)
- 인물 본문은 정규식 기반 시트 뷰를 위해 **정체성**, **종족/직업/레벨**, **능력치**, **HP/AC**, **배경·성향**, **기술·장비**, **목표·관계·약점**, **연도별 특기사항**을 각각 독립된 굵은 구획명으로 사용하세요.
- 장소: 유형/지형/규모, 분위기, 주요 구역, 주민/세력, 자원·위험, 비밀·모험 훅, 접근 경로, 연도별 변화
- 세력: 목표·조직·자원·지도자·동맹/적대·현재 계획·약점·연도별 사건
- 사건/연도: 발생 연도, 원인, 전개, 결과, 세계관 영향, 관련 인물·장소·세력
모든 엔트리는 공개 본문 content와 비밀 정보 secret을 분리하세요. secret의 사실은 content에 암시하거나 반복하지 마세요. 다른 엔트리의 공개 설정에도 비밀을 근거로 사용하거나 크게 언급하지 마세요.
연도와 관련된 항목은 "## 연도별 특기사항"에 해당 시점 사건을 기록하세요.
설계 목록 밖의 새 항목을 추가하지 말고, Markdown 제목은 쓰지 마세요.
JSON만 출력: {"entries":[{"title":"", "category":"", "content":"", "secret":"클릭해서만 볼 비밀 설정", "references":[], "attributes":{"축이름":{"value":1,"description":"이 인물에게 이 수치가 갖는 구체적 의미"}}, "reused_skill_ids":[], "new_skill_proposals":[{"name":"","description":"","type":"스킬","tags":[]}]}]}
"""

def _world_metadata_rules(world_id):
    if not world_id: return ""
    try:
        from models import WorldAttributeSchema, WorldSkillRegistry, WorldGuideline, WorldEntryTemplate
        attrs=[a.to_dict() for a in WorldAttributeSchema.query.filter_by(world_id=world_id,is_active=True).order_by(WorldAttributeSchema.axis_order).all()]
        skills=[s.to_dict() for s in WorldSkillRegistry.query.filter_by(world_id=world_id).limit(80).all()]
        templates=[t.to_dict() for t in WorldEntryTemplate.query.filter_by(world_id=world_id,is_active=True).all()]
        guide=WorldGuideline.query.get(world_id)
        return "=== 세계관 고정 메타데이터 ===\n능력치 축(범위 안의 숫자와 인물별 구체적 설명 반환): "+json.dumps(attrs,ensure_ascii=False)+"\n카테고리 템플릿(필수 필드 준수): "+json.dumps(templates,ensure_ascii=False)+"\n기존 스킬/특성(맞으면 ID 재사용): "+json.dumps(skills,ensure_ascii=False)+"\n스킬 가이드: "+(guide.skill_generation_guide if guide else "")+"\n특성 가이드: "+(guide.trait_generation_guide if guide else "")
    except Exception:
        return ""


def generate_world_detail_batch(context: str, items: list, existing_entries: list, max_chars: int = 0, world_id=None) -> dict:
    client, model = get_llm_client()
    existing = serialize_world_state(existing_entries, max_chars=max_chars) if existing_entries else "(없음)"
    items_text = json.dumps(items, ensure_ascii=False)
    length_rule = ("content 길이는 제한하지 마세요. 성급히 요약하거나 짧게 끝내지 말고, 특별한 이유가 없으면 엔트리마다 최소 1500자 이상으로 모든 필수 구획·역사·관계·갈등·구체적 사례를 충실히 작성하세요." if not max_chars else f"각 content는 최대 {max_chars}자 이내로 작성하세요. 제한 안에서 정의·배경/역사·구조/특성·관계·갈등을 우선순위대로 충실히 담으세요.")
    prompt = f"=== 큰 맥락 ===\n{context}\n\n=== 이번에 상세 작성할 설계 항목 ===\n{items_text}\n\n=== 기존 DB 참고 ===\n{existing}"
    world_id = world_id or next((e.get("world_id") for e in existing_entries if e.get("world_id")), None)
    system_prompt = WORLD_DETAIL_PROMPT.replace("{length_rule}", length_rule).replace("{metadata_rules}", _world_metadata_rules(world_id))
    raw = client.chat.completions.create(model=model, messages=[{"role":"system","content":system_prompt},{"role":"user","content":prompt}], temperature=0.6, max_tokens=get_max_output_tokens()).choices[0].message.content or ""
    result = _parse_json_safe(raw, "world_detail_batch")
    if max_chars:
        for entry in result.get("entries", []):
            entry["content"] = str(entry.get("content") or "")[:max_chars]
    result["_raw"] = raw
    return result


def generate_novel_text(world_id: int, instruction: str, chapter: dict = None, entry_ids: list = None, mode: str = "continue", context_text: str = "", situation_text: str = "") -> dict:
    from models import AppSettings, WorldEntry, NovelChapter, EntryAttributeValue, WorldAttributeSchema, EntrySkillLink, WorldSkillRegistry
    client, model=get_llm_client()
    settings=AppSettings.get();style={"pov":settings.novel_pov or "3인칭 관찰자","tone_guide":settings.novel_tone_guide or "","forbidden_expressions":settings.novel_forbidden_expressions or "","sample_text":settings.novel_sample_text or ""}
    entries_q=WorldEntry.query.filter_by(world_id=world_id,is_active=True)
    if entry_ids is not None:entries_q=entries_q.filter(WorldEntry.id.in_(entry_ids or [-1]))
    entry_rows=entries_q.limit(30).all();entries=[e.to_dict() for e in entry_rows];stat_lines=[]
    for e in entry_rows:
        attrs=[]
        for v in EntryAttributeValue.query.filter_by(entry_id=e.id).all():
            axis=WorldAttributeSchema.query.get(v.axis_id)
            if axis:attrs.append(f"{axis.axis_name}:{v.value}")
        skills=[]
        for link in EntrySkillLink.query.filter_by(entry_id=e.id).all():
            skill=WorldSkillRegistry.query.get(link.skill_id)
            if skill:skills.append(skill.name+(f"({link.rank})" if link.rank else ""))
        if attrs or skills:stat_lines.append(f"{e.title} | 능력치 {', '.join(attrs)} | 스킬/특성 {', '.join(skills)}")
    ghostwrite=mode=="ghostwrite"
    search_text=(context_text+" "+situation_text) if ghostwrite else (instruction+" "+(chapter or {}).get("content","")[-1500:])
    all_past=NovelChapter.query.filter_by(world_id=world_id,part_id=((chapter or {}).get("part") or {}).get("id")).filter(NovelChapter.id!=(chapter or {}).get("id")).all();query_words=_extract_context_words(search_text)
    past=sorted(all_past,key=lambda c:len(_extract_context_words(c.title+" "+c.content)&query_words),reverse=True)[:3]
    style_text=json.dumps(style,ensure_ascii=False)
    part_context=json.dumps((chapter or {}).get("part") or {},ensure_ascii=False)
    shared=f"문체 설정: {style_text}\n\n현재 이야기/부의 상위 설정:\n{part_context}\n\n등장 엔트리:\n{serialize_world_state(entries,get_entry_char_limit())}\n\n능력치/스킬 시트:\n"+"\n".join(stat_lines)+"\n\n관련 과거 챕터:\n"+"\n".join(f"[{c.title}] {c.content}" for c in past)
    if ghostwrite:
        prompt=f"{shared}\n\n현재까지 작성된 본문(설정·인물·말투 일관성 참고용):\n{(chapter or {}).get('content','') or '(없음)'}\n\n작성할 맥락:\n{context_text}\n\n작성할 상황:\n{situation_text}"
        system="세계관 설정과 공개 범위를 존중하며 사용자를 대신해 장면을 완성하는 대필 작가입니다. 사용자가 준 '맥락'과 '상황'을 새로 쓸 장면의 근거로 삼되, '현재까지 작성된 본문'을 참고해 기존 설정·인물·말투·시점과 모순되지 않게 쓰세요. 지정되지 않은 사건이나 설정을 임의로 추가하지 말고, 문체 설정과 금지 표현, 인물 말투를 지키세요. 기존 DB에 없는 새 고유명사를 발견/창작하면 별도 후보로 분리하세요. JSON만 출력: {\"content\":\"Markdown 본문\",\"new_entity_proposals\":[{\"title\":\"\",\"category\":\"인물/장소/세력 등\",\"content\":\"등록 초안\"}]}"
    else:
        prompt=f"{shared}\n\n현재 본문:\n{(chapter or {}).get('content','')}\n\n요청:\n{instruction}"
        system="세계관 설정과 공개 범위를 존중하는 소설 작가입니다. 금지 표현과 인물 말투를 지키세요. 기존 DB에 없는 새 고유명사를 발견/창작하면 별도 후보로 분리하세요. JSON만 출력: {\"content\":\"Markdown 본문\",\"new_entity_proposals\":[{\"title\":\"\",\"category\":\"인물/장소/세력 등\",\"content\":\"등록 초안\"}]}"
    response=client.chat.completions.create(model=model,messages=[{"role":"system","content":system},{"role":"user","content":prompt}],temperature=.75,max_tokens=get_max_output_tokens())
    raw=response.choices[0].message.content or "";parsed=_parse_json_safe(raw,"novel_generate")
    return {"proposal":parsed.get("content",raw) if isinstance(parsed,dict) else raw,"new_entity_proposals":parsed.get("new_entity_proposals",[]) if isinstance(parsed,dict) else [],"model":model}


def propose_novel_mentions(content: str, entries: list) -> dict:
    client,model=get_llm_client();catalog=[{"id":e["id"],"title":e["title"],"aliases":e.get("aliases",[])} for e in entries]
    prompt="본문에서 대명사나 문맥 지칭이 어떤 엔트리를 뜻하는지 제안하세요. 정확한 본문 문자열과 0-based start/end를 반환하세요. 확실하지 않으면 제외. JSON만 출력: {\"mentions\":[{\"entry_id\":1,\"matched_text\":\"그 여자\",\"span_start\":0,\"span_end\":4}]}\n엔트리:"+json.dumps(catalog,ensure_ascii=False)+"\n본문:"+content
    raw=client.chat.completions.create(model=model,messages=[{"role":"user","content":prompt}],temperature=.2,max_tokens=1500).choices[0].message.content or ""
    return _parse_json_safe(raw,"novel_mentions")


ENTRY_REVISION_PROMPT = """당신은 세계관 편집 파트너입니다. 사용자의 요청에 맞게 기존 엔트리를 수정하는 방안을 대화형으로 제안하세요.
세계관의 이미 확정된 사실을 임의로 바꾸지 말고, 요청이 모호하면 questions에 확인 질문을 넣으세요. 저장은 절대 수행하지 않습니다.
반드시 JSON만 출력하세요:
{"reply":"사용자에게 보여 줄 설명","questions":["확인 질문"],"changes":["변경 요약"],"proposal":{"title":"수정 제목","category":"기존 또는 허용 분류","content":"수정 제안 전체 내용","references":[1]}}
proposal은 충분히 수정안을 제시할 수 있을 때만 넣고, 아직 질문이 필요한 경우 null로 두세요.
"""


def propose_entry_revision(entry: dict, instruction: str, history: list = None) -> dict:
    client, model = get_llm_client()
    past = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in (history or [])[-8:]) or "(없음)"
    prompt = f"=== 현재 엔트리 ===\nID: {entry.get('id')}\n제목: {entry.get('title')}\n분류: {entry.get('category')}\n내용:\n{entry.get('content')}\n참조: {entry.get('references', [])}\n\n=== 이전 대화 ===\n{past}\n\n=== 이번 요청 ===\n{instruction}"
    raw = client.chat.completions.create(model=model, messages=[{"role":"system","content":ENTRY_REVISION_PROMPT},{"role":"user","content":prompt}], temperature=0.45, max_tokens=get_max_output_tokens()).choices[0].message.content or ""
    result = _parse_json_safe(raw, "entry_revision")
    if not isinstance(result, dict): result = {"reply": raw, "questions": [], "proposal": None}
    return result


def propose_entry_from_chat(world_id: int, history: list, entries: list, instruction: str = "", model_override: str = None) -> dict:
    """아이디어 대화에서 한 개의 저장 전 엔트리 초안을 만든다."""
    client, model = get_llm_client(model_override)
    conversation = "\n\n".join(
        f"{'사용자' if m.get('role') == 'user' else '기획 파트너'}:\n{str(m.get('content') or '')}"
        for m in (history or [])
    )
    length_rule = ("본문 길이를 임의로 줄이지 말고 대화에서 확정된 내용을 충분히 구조화하세요."
                   if not get_entry_char_limit() else f"content는 최대 {get_entry_char_limit()}자 이내로 작성하세요.")
    system = f"""당신은 세계관 DB 편집자입니다. 아이디어 대화에서 사용자가 확정하거나 유력하게 채택한 내용만 하나의 엔트리 초안으로 정리하세요.
아직 논의 중인 대안은 사실처럼 합치지 말고, 대화에 근거가 부족한 세부사항을 새로 발명하지 마세요. {length_rule}
content는 읽기 쉬운 Markdown으로 작성하세요. 비밀은 공개 content에 암시·반복하지 말고 secret에만 분리하세요.
{_world_metadata_rules(world_id)}
JSON만 출력: {{"title":"", "category":"세력·인물·관념·물건·종족·사건·장소·마법/기술·신화/종교·역사/기록·규칙/법·연도 중 하나", "content":"Markdown 본문", "secret":"", "aliases":[], "attributes":{{"능력치 축":{{"value":1,"description":"구체적 의미"}}}}}}"""
    prompt = f"=== 현재 공개 DB 참고 ===\n{serialize_world_state(entries, get_entry_char_limit())}\n\n=== 아이디어 대화 ===\n{conversation}\n\n=== 추가 지시 ===\n{instruction or '(없음)'}"
    raw = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.35,
        max_tokens=get_max_output_tokens(),
    ).choices[0].message.content or ""
    result = _parse_json_safe(raw, "chat_entry_draft")
    if not isinstance(result, dict):
        raise ValueError("엔트리 초안을 JSON으로 해석하지 못했습니다.")
    return result.get("entry", result) if isinstance(result.get("entry", result), dict) else result


def propose_entry_skills(entry: dict, candidates: list, guideline: dict) -> dict:
    client,model=get_llm_client()
    prompt="캐릭터에 맞는 스킬/특성을 제안하세요. 기존 목록이 맞으면 ID를 재사용하고 없을 때만 신규 제안하세요. JSON만 출력: {\"reused_skill_ids\":[],\"new_skill_proposals\":[{\"name\":\"\",\"description\":\"\",\"type\":\"스킬\",\"tags\":[],\"rarity\":\"일반\"}]}\n\n엔트리:"+json.dumps(entry,ensure_ascii=False)+"\n후보:"+json.dumps(candidates,ensure_ascii=False)+"\n가이드:"+json.dumps(guideline,ensure_ascii=False)
    raw=client.chat.completions.create(model=model,messages=[{"role":"user","content":prompt}],temperature=.4,max_tokens=1500).choices[0].message.content or ""
    return _parse_json_safe(raw,"skill_proposal")


def propose_metadata_migration(entries: list, metadata_rules: str, instruction: str = "", required_axes: list = None) -> dict:
    client,model=get_llm_client();required_axes=required_axes or []
    exact_rule=("\n아래 '반드시 완성할 기존 축'이 있으면 새 축을 만들거나 기존 축을 누락·개명하지 마세요. 정확히 같은 axis_name, min_tier, max_tier를 사용하고 모든 축을 하나도 빠짐없이 반환하세요. 각 인물의 attributes에도 모든 기존 축을 빠짐없이 넣으세요.\n반드시 완성할 기존 축:\n"+json.dumps([{"axis_name":a.get("axis_name"),"min_tier":a.get("min_tier",1),"max_tier":a.get("max_tier",5),"description":a.get("description",""),"tier_descriptions":a.get("tier_descriptions") or a.get("tier_labels") or {}} for a in required_axes],ensure_ascii=False)) if required_axes else ""
    prompt="""기존 인물 엔트리를 분석해 D&D/TRPG용 메타데이터 보강안을 만드세요. 원문과 DB는 수정하지 말고 제안만 반환하세요.
기존 능력치 축이 없거나 설명이 부족하면 세계관에 맞는 축을 제안하고, 각 축의 용도·수치 범위·모든 단계의 의미를 상세히 작성하세요.
min_tier부터 max_tier까지 한 단계도 빠뜨리지 말고 tier_descriptions에 정확히 하나씩 작성하세요. 예를 들어 1~10 범위라면 "1"부터 "10"까지 정확히 10개의 키가 있어야 합니다.
각 단계는 추상적인 강약 표현만 쓰지 말고 그 단계에서 가능한 행동, 한계 또는 판정 결과가 드러나게 서술하며, 단계가 올라갈수록 일관되게 강해져야 합니다.
각 인물에는 축 범위 안의 정수 수치와, 본문의 어떤 설정 때문에 그 수치인지 구체적인 설명을 작성하세요. 기존 스킬은 ID를 재사용하세요.
JSON만 출력:
{"attribute_schemas":[{"axis_name":"힘","min_tier":1,"max_tier":10,"description":"축의 판정 용도와 의미","tier_descriptions":{"1":"무거운 물건을 거의 들지 못한다.","2":"일상적인 짐을 드는 데 어려움이 있다.","3":"평균 이하의 힘으로 가벼운 짐을 다룬다.","4":"가벼운 육체노동을 수행한다.","5":"평범한 성인 수준의 힘이다.","6":"꾸준히 단련한 사람 수준이다.","7":"무거운 장비를 오래 다룬다.","8":"여러 사람 몫의 힘을 발휘한다.","9":"인간의 일반적인 한계에 가깝다.","10":"세계관에서 허용되는 최고 수준의 힘이다."}}],"entries":[{"entry_id":1,"attributes":{"힘":{"value":7,"description":"훈련된 용병이라 무거운 장비를 장시간 다룬다."}},"reused_skill_ids":[]}]}
"""+exact_rule+"\n"+metadata_rules+"\n추가 요청:"+(instruction or "세계관 설정과 인물 본문을 근거로 빠짐없이 작성")+"\n인물 엔트리:"+json.dumps(entries,ensure_ascii=False)
    raw=client.chat.completions.create(model=model,messages=[{"role":"user","content":prompt}],temperature=.2,max_tokens=get_max_output_tokens()).choices[0].message.content or ""
    result=_parse_json_safe(raw,"metadata_migration")
    if not isinstance(result,dict):result={"attribute_schemas":[],"entries":[]}
    raw_parts=[raw]

    if required_axes:
        key=lambda value:"".join(str(value or "").split()).casefold()
        required={key(a.get("axis_name")):a for a in required_axes if a.get("axis_name")}
        entry_ids={int(e.get("id")) for e in entries if e.get("id") is not None}

        def canonicalize(payload):
            schemas=[]
            for schema in payload.get("attribute_schemas") or []:
                if not isinstance(schema,dict):continue
                original=required.get(key(schema.get("axis_name")))
                if not original:continue
                fixed=dict(schema);fixed["axis_name"]=original.get("axis_name");fixed["min_tier"]=original.get("min_tier",1);fixed["max_tier"]=original.get("max_tier",5);schemas.append(fixed)
            rows=[]
            for row in payload.get("entries") or []:
                if not isinstance(row,dict):continue
                try:entry_id=int(row.get("entry_id"))
                except (TypeError,ValueError):continue
                if entry_id not in entry_ids:continue
                attrs={}
                for name,value in (row.get("attributes") or {}).items():
                    original=required.get(key(name))
                    if original:attrs[original.get("axis_name")]=value
                fixed=dict(row);fixed["entry_id"]=entry_id;fixed["attributes"]=attrs;rows.append(fixed)
            return schemas,rows

        schemas,rows=canonicalize(result)
        schema_keys={key(x.get("axis_name")) for x in schemas}
        attrs_by_entry={x["entry_id"]:dict(x.get("attributes") or {}) for x in rows}
        missing_schema=[a.get("axis_name") for k,a in required.items() if k not in schema_keys]
        missing_values={eid:[a.get("axis_name") for k,a in required.items() if a.get("axis_name") not in attrs_by_entry.get(eid,{})] for eid in entry_ids}
        focus=sorted(set(missing_schema+[name for names in missing_values.values() for name in names]))
        if focus:
            retry_prompt="이전 응답에서 저장된 능력치 축 또는 인물별 값이 누락되었습니다. 아래 누락 축만 보충하되 axis_name과 범위를 정확히 지키세요. 모든 인물에 누락 축의 수치와 근거를 작성하세요. JSON 형식은 이전 요청과 동일합니다.\n누락 축: "+json.dumps([a for a in required_axes if a.get("axis_name") in focus],ensure_ascii=False)+"\n인물 엔트리: "+json.dumps(entries,ensure_ascii=False)
            retry_raw=client.chat.completions.create(model=model,messages=[{"role":"user","content":retry_prompt}],temperature=.1,max_tokens=get_max_output_tokens()).choices[0].message.content or ""
            raw_parts.append(retry_raw);retry=_parse_json_safe(retry_raw,"metadata_migration_retry")
            retry_schemas,retry_rows=canonicalize(retry if isinstance(retry,dict) else {})
            by_schema={key(x.get("axis_name")):x for x in schemas}
            for item in retry_schemas:by_schema[key(item.get("axis_name"))]=item
            schemas=list(by_schema.values())
            by_entry={x["entry_id"]:x for x in rows}
            for item in retry_rows:
                target=by_entry.setdefault(item["entry_id"],{"entry_id":item["entry_id"],"attributes":{},"reused_skill_ids":[]})
                target.setdefault("attributes",{}).update(item.get("attributes") or {})
            rows=list(by_entry.values())
        final_schema_keys={key(x.get("axis_name")) for x in schemas}
        final_attrs={x["entry_id"]:x.get("attributes") or {} for x in rows}
        result["attribute_schemas"]=schemas;result["entries"]=rows
        result["missing_axes_after_retry"]=[a.get("axis_name") for k,a in required.items() if k not in final_schema_keys]
        result["missing_values_after_retry"]={str(eid):[a.get("axis_name") for a in required_axes if a.get("axis_name") not in final_attrs.get(eid,{})] for eid in entry_ids}
        result["missing_values_after_retry"]={k:v for k,v in result["missing_values_after_retry"].items() if v}
    result["_raw"]="\n\n===== 자동 보충 재요청 =====\n\n".join(raw_parts)
    return result


def propose_character_attribute_fill(entries: list, required_axes: list, instruction: str = "") -> dict:
    """기존 스키마는 출력하지 않고 선택된 인물의 능력치 값과 근거만 제안한다."""
    client,model=get_llm_client();key=lambda value:"".join(str(value or "").split()).casefold()
    axes={key(a.get("axis_name")):a for a in (required_axes or []) if a.get("axis_name")}
    entry_ids={int(e.get("id")) for e in (entries or []) if e.get("id") is not None}
    compact_axes=[{"axis_name":a.get("axis_name"),"min_tier":a.get("min_tier",1),"max_tier":a.get("max_tier",5),"description":a.get("description",""),"tier_descriptions":a.get("tier_descriptions") or a.get("tier_labels") or {}} for a in required_axes]
    base="""당신은 TRPG 캐릭터 능력치 판정자입니다. 제공된 기존 능력치 축으로 선택된 인물의 수치와 인물별 근거만 작성하세요.
능력치 스키마나 단계 설명을 다시 출력하지 마세요. 모든 entry_id와 axis_name을 입력과 정확히 유지하고, 각 인물에 모든 축을 빠짐없이 배정하세요.
value는 해당 축의 min_tier~max_tier 범위 정수여야 합니다. description에는 인물 본문의 어떤 설정 때문에 그 수치인지 구체적으로 작성하세요.
JSON만 출력: {"entries":[{"entry_id":1,"attributes":{"힘":{"value":7,"description":"용병 훈련으로 무거운 장비를 오래 다룬다."}}}]}
"""
    def call(prompt,label):
        raw=client.chat.completions.create(model=model,messages=[{"role":"user","content":prompt}],temperature=.15,max_tokens=get_max_output_tokens()).choices[0].message.content or ""
        parsed=_parse_json_safe(raw,label);return (parsed if isinstance(parsed,dict) else {}),raw
    def normalize(payload):
        rows={}
        for item in payload.get("entries") or []:
            if not isinstance(item,dict):continue
            try:entry_id=int(item.get("entry_id"))
            except (TypeError,ValueError):continue
            if entry_id not in entry_ids:continue
            attrs={}
            for name,value in (item.get("attributes") or {}).items():
                original=axes.get(key(name))
                if not original:continue
                attrs[original.get("axis_name")]=value
            rows[entry_id]={"entry_id":entry_id,"attributes":attrs,"reused_skill_ids":[]}
        return rows
    def missing(rows):
        return {eid:[a.get("axis_name") for a in required_axes if a.get("axis_name") not in rows.get(eid,{}).get("attributes",{})] for eid in entry_ids if any(a.get("axis_name") not in rows.get(eid,{}).get("attributes",{}) for a in required_axes)}
    prompt=base+"\n기존 능력치 축:\n"+json.dumps(compact_axes,ensure_ascii=False)+"\n선택된 인물:\n"+json.dumps(entries,ensure_ascii=False)+"\n추가 요청:\n"+(instruction or "인물 본문을 근거로 배정")
    first,raw=call(prompt,"character_attribute_fill");rows=normalize(first);raw_parts=[raw];missing_values=missing(rows)
    if missing_values:
        retry_prompt=base+"\n이전 응답에서 아래 인물·축 값이 누락되었습니다. 누락된 값만 보충하세요:\n"+json.dumps(missing_values,ensure_ascii=False)+"\n기존 능력치 축:\n"+json.dumps(compact_axes,ensure_ascii=False)+"\n선택된 인물:\n"+json.dumps(entries,ensure_ascii=False)
        retry,raw2=call(retry_prompt,"character_attribute_fill_retry");raw_parts.append(raw2)
        for entry_id,item in normalize(retry).items():
            target=rows.setdefault(entry_id,{"entry_id":entry_id,"attributes":{},"reused_skill_ids":[]});target["attributes"].update(item.get("attributes") or {})
        missing_values=missing(rows)
    return {"attribute_schemas":[],"entries":list(rows.values()),"missing_values_after_retry":{str(k):v for k,v in missing_values.items()},"_raw":"\n\n===== 자동 보충 재요청 =====\n\n".join(raw_parts)}


def propose_attribute_schema_fill(required_axes: list, metadata_rules: str = "", instruction: str = "") -> dict:
    """저장된 축을 개명하지 않고 비어 있는 설명과 단계 서술만 완성한다."""
    client,model=get_llm_client();key=lambda value:"".join(str(value or "").split()).casefold()
    required={key(a.get("axis_name")):a for a in (required_axes or []) if a.get("axis_name")}
    base="""당신은 TRPG 능력치 스키마 설계자입니다. 캐릭터 수치를 배정하지 말고, 제공된 능력치 축의 빈 설명만 작성하세요.
축을 추가·삭제·개명하지 말고 axis_name을 입력과 정확히 같게 유지하세요. 요청하지 않은 필드는 출력하지 마세요.
missing_description이 true이면 description에 이 축의 판정 용도와 수치 의미를 작성하세요. missing_tiers에 적힌 번호만 tier_descriptions에 작성하세요.
각 단계는 단순히 '낮음/보통/높음'이라고 하지 말고 가능한 행동, 성공 범위, 한계가 드러나게 쓰며 단계가 올라갈수록 일관되게 향상되어야 합니다.
JSON만 출력: {"attribute_schemas":[{"axis_name":"힘","description":"판정 용도","tier_descriptions":{"2":"2단계 행동과 한계"}}]}
"""

    def call(text,label):
        raw=client.chat.completions.create(model=model,messages=[{"role":"user","content":text}],temperature=.2,max_tokens=get_max_output_tokens()).choices[0].message.content or ""
        parsed=_parse_json_safe(raw,label);return (parsed if isinstance(parsed,dict) else {}),raw

    merged={}
    for k,original in required.items():
        min_tier=int(original.get("min_tier",1));max_tier=int(original.get("max_tier",5));tiers=original.get("tier_descriptions") or original.get("tier_labels") or {}
        merged[k]={"axis_name":original.get("axis_name"),"min_tier":min_tier,"max_tier":max_tier,"description":str(original.get("description") or "").strip(),"tier_descriptions":{str(n):str(tiers.get(str(n),tiers.get(n,"")) or "").strip() for n in range(min_tier,max_tier+1)}}

    def merge_payload(payload):
        for schema in payload.get("attribute_schemas") or []:
            if not isinstance(schema,dict):continue
            original=required.get(key(schema.get("axis_name")))
            if not original:continue
            target=merged[key(original.get("axis_name"))]
            if not target["description"] and str(schema.get("description") or "").strip():target["description"]=str(schema.get("description")).strip()
            tiers=schema.get("tier_descriptions") if isinstance(schema.get("tier_descriptions"),dict) else schema.get("tier_labels")
            if not isinstance(tiers,dict):tiers={}
            for n in range(target["min_tier"],target["max_tier"]+1):
                value=str(tiers.get(str(n),tiers.get(n,"")) or "").strip()
                if not target["tier_descriptions"][str(n)] and value:target["tier_descriptions"][str(n)]=value

    def incomplete_keys():
        missing=[]
        for k,original in required.items():
            schema=merged.get(k)
            if not schema or not schema.get("description") or any(not schema.get("tier_descriptions",{}).get(str(n)) for n in range(int(original.get("min_tier",1)),int(original.get("max_tier",5))+1)):missing.append(k)
        return missing

    def missing_request(keys):
        rows=[]
        for k in keys:
            schema=merged[k]
            rows.append({"axis_name":schema["axis_name"],"missing_description":not bool(schema["description"]),"missing_tiers":[n for n in range(schema["min_tier"],schema["max_tier"]+1) if not schema["tier_descriptions"].get(str(n))]})
        return rows

    missing=incomplete_keys()
    if not missing:return {"attribute_schemas":list(merged.values()),"missing_axes_after_retry":[],"_raw":""}
    prompt=base+"\n채울 빈 필드:\n"+json.dumps(missing_request(missing),ensure_ascii=False)+"\n세계관 메타데이터:\n"+metadata_rules+"\n추가 요청:\n"+(instruction or "세계관의 척도에 맞게 구체적으로 작성")
    first,raw=call(prompt,"attribute_schema_fill");merge_payload(first);raw_parts=[raw];missing=incomplete_keys()
    if missing:
        retry,raw2=call(base+"\n이전 응답에서 아래 빈 필드가 누락되었습니다. 이것만 보충하세요:\n"+json.dumps(missing_request(missing),ensure_ascii=False)+"\n추가 요청:\n"+(instruction or "구체적으로 작성"),"attribute_schema_fill_retry")
        raw_parts.append(raw2);merge_payload(retry);missing=incomplete_keys()
    return {"attribute_schemas":list(merged.values()),"missing_axes_after_retry":[required[k].get("axis_name") for k in missing],"_raw":"\n\n===== 자동 보충 재요청 =====\n\n".join(raw_parts)}


def serialize_world_state(entries: list, max_chars: int = 0) -> str:
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
            # 세계관 설계의 아직 DB에 저장되지 않은 임시 엔트리는 id가 없을 수 있다.
            item_id = item.get("id", "임시")
            year_tag = f" [연도: {item.get('primary_year')}]" if item.get("primary_year") else ""
            lines.append(f"  ID={item_id} | {item['title']}{creator_tag}{version_tag}{year_tag}{ref_str}")
            lines.append(f"    {content}")

    return "\n".join(lines)


def estimate_world_tokens(entries: list, config: dict = None) -> dict:
    """현재 세계관 + 프롬프트의 예상 토큰 수 반환"""
    max_chars = (config.get("max_content_chars") if config.get("max_content_chars") is not None else 0) if config else 0
    world_state = serialize_world_state(entries, max_chars=max_chars)
    world_tokens = estimate_tokens(world_state)

    prompt_tokens = 0
    if config:
        system = SYSTEM_PROMPT_TEMPLATE.format(
            prompt_level_1=config.get("prompt_level_1") or "없음",
            prompt_level_2=config.get("prompt_level_2") or "없음",
            prompt_level_3=config.get("prompt_level_3") or "없음",
            entries_per_tick=config.get("entries_per_tick", 1),
            entry_length_rule=("제한 없음. 정보가 충분할 때까지 작성" if not max_chars else f"항목당 최대 {max_chars}자"),
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
    max_chars = config.get("max_content_chars") if config.get("max_content_chars") is not None else 500
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
        entries_per_tick=config.get("entries_per_tick", 1),
        entry_length_rule=("제한 없음. 정보가 충분할 때까지 작성" if not max_chars else f"항목당 최대 {max_chars}자"),
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
        max_tokens=get_max_output_tokens(),
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
    max_chars = config.get("max_content_chars") if config.get("max_content_chars") is not None else 0
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
        max_tokens=get_max_output_tokens(),
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
        max_tokens=get_max_output_tokens(),
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


def generate_entry(title: str, category: str, hint: str, ref_entries: list, metadata_rules: str = "", max_chars: int = None) -> dict:
    """유저 입력(제목·분류·힌트·참조)을 기반으로 엔트리 내용을 LLM이 생성"""
    client, model = get_llm_client()

    max_chars=get_entry_char_limit() if max_chars is None else max(0,int(max_chars))
    ref_block = ""
    if ref_entries:
        lines = []
        for e in ref_entries:
            ref_content=str(e.get("content") or "")
            if max_chars:ref_content=ref_content[:max_chars]
            lines.append(f"  ID={e.get('id')} [{e['category']}] {e['title']}: {ref_content}")
        ref_block = "\n참조 엔트리:\n" + "\n".join(lines)

    hint_block = f"\n사용자 힌트/초안:\n{hint}" if hint else ""

    prompt = f"""다음 정보를 바탕으로 세계관 엔트리의 상세 내용을 작성해주세요.

제목: {title}
분류: {category}{hint_block}{ref_block}
{metadata_rules}

요구 사항:
- 세계관 설정에 어울리는 구체적이고 풍부한 묘사
- 참조 엔트리와 자연스럽게 연결되는 내용
- 인물이면 위 메타데이터의 활성 능력치 축을 사용해 attributes에 범위 안의 정수와 인물별 근거를 반환
- 공개 본문과 비밀을 분리하고, secret의 내용을 content에 암시하거나 반복하지 않기
- 공개 content는 {('제한 없이 성급히 요약하지 말고 특별한 이유가 없으면 최소 1500자 이상' if not max_chars else f'최대 {max_chars}자 이내')}로 구체적으로 작성
- 마크다운 기호(**볼드**, # 헤더 등) 사용 금지, 일반 텍스트만
- 한국어로 작성
- 반드시 JSON으로만 응답: {{"content":"생성된 공개 내용","secret":"클릭해서만 볼 비밀 설정","attributes":{{"능력치명":{{"value":1,"description":"수치의 근거"}}}}}}"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=get_max_output_tokens(),
    )
    raw = response.choices[0].message.content.strip()
    parsed = _parse_json_safe(raw, "generate_entry")
    normalized = _normalize_generated_entry_response(raw, parsed)
    content = normalized["content"]
    if not content:raise ValueError("LLM이 공개 본문을 반환하지 않았습니다.")
    if max_chars:content=content[:max_chars]
    tokens_in = getattr(response.usage, "prompt_tokens", 0)
    tokens_out = getattr(response.usage, "completion_tokens", 0)
    return {
        "content": content,
        "secret": normalized["secret"][:10000],
        "attributes": normalized["attributes"],
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
        max_chars=get_entry_char_limit(),
    )

    tmpl = overrides.get("timeline_generate", TIMELINE_GEN_PROMPT)
    prompt = tmpl.format(
        category=entry.get("category", ""),
        title=entry.get("title", ""),
        content=entry.get("content", ""),
        world_state=world_state,
        extra_prompt=extra_prompt or f"이 엔트리의 주요 사건을 {episode_count}개의 에피소드로 구성하세요.",
    )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=get_max_output_tokens(),
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
