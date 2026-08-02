import time

from openai import OpenAI

DEFAULT_BASE_URL = "http://192.168.0.116:8000/v1"
AVAILABLE_MODELS = [
    "mlx-community--gemma-4-26b-a4b-it-8bit",
    "Qwen3.6-35B-A3B-4bit",
    "gpt-oss-20b-MXFP4-Q8",
]
DEFAULT_MODEL = AVAILABLE_MODELS[0]

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODELS = ["deepseek-v4-pro", "deepseek-v4-flash"]

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5"]

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GOOGLE_MODELS = ["gemini-3.5-pro", "gemini-3.5-flash", "gemini-3.1-pro", "gemini-3.1-flash"]

# Lightweight local llama.cpp-style endpoint (e.g. a smaller gemma model dedicated to
# fast decompose/select-style calls, run alongside the heavier local server above).
# base_url/model are editable in the UI - these are just starting defaults.
LOCAL_LIGHT_BASE_URL = "http://192.168.0.116:8080/v1"
LOCAL_LIGHT_MODELS = ["gemma4"]

PROVIDERS = {
    "로컬 서버 (OpenAI 호환)": {"base_url": DEFAULT_BASE_URL, "models": AVAILABLE_MODELS},
    "로컬 경량 LLM (분해/선별용)": {"base_url": LOCAL_LIGHT_BASE_URL, "models": LOCAL_LIGHT_MODELS},
    "DeepSeek API": {"base_url": DEEPSEEK_BASE_URL, "models": DEEPSEEK_MODELS},
    "Anthropic (Claude)": {"base_url": ANTHROPIC_BASE_URL, "models": ANTHROPIC_MODELS},
    "Google (Gemini)": {"base_url": GOOGLE_BASE_URL, "models": GOOGLE_MODELS},
}

MAX_TOKENS = 32768
MAX_HISTORY_MESSAGES = 20
REQUEST_TIMEOUT = 300


def trim_history(history: list) -> list:
    if not history:
        return []
    return history[-MAX_HISTORY_MESSAGES:]


# Substrings that mark a model id as almost certainly NOT a text chat model (embeddings,
# image/video/audio generation, TTS, etc). External catalogs like Google's /models mix
# these in with hundreds of chat models; a local llama.cpp-style server normally only
# exposes what its operator actually loaded, so this mainly protects against big
# multi-provider catalogs, not local servers.
_NON_CHAT_MODEL_MARKERS = (
    "embedding", "embed-", "aqa", "imagen", "veo", "tts", "audio", "vision-only",
    "image-generation", "moderation", "whisper", "dall-e", "clip",
)


def _is_google(base_url: str) -> bool:
    return bool(base_url) and "generativelanguage.googleapis.com" in base_url


def list_models(api_key: str, base_url: str) -> list:
    """Returns a list of model id strings available on the chat server, filtered to
    exclude obviously non-chat models (embeddings, image/audio generation, etc) so a
    provider's full catalog doesn't drown out the handful of models actually usable
    for chat completions here."""
    if _is_google(base_url):
        import gemini_client
        return gemini_client.list_models(api_key, base_url)
    client = _client(api_key, base_url)
    resp = client.models.list()
    ids = sorted(m.id for m in resp.data)
    chat_ids = [i for i in ids if not any(marker in i.lower() for marker in _NON_CHAT_MODEL_MARKERS)]
    return chat_ids or ids


def _client(api_key: str, base_url: str) -> OpenAI:
    return OpenAI(
        api_key=api_key or "local",
        base_url=base_url or DEFAULT_BASE_URL,
        timeout=REQUEST_TIMEOUT,
    )


def _log_usage(prefix: str, usage) -> None:
    if not usage:
        return
    cached = None
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
    if cached:
        print(f"[llm] {prefix} usage={usage} cache_hit_tokens={cached}")
    else:
        print(f"[llm] {prefix} usage={usage}")


def _usage_dict(usage, elapsed: float) -> dict:
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "elapsed": elapsed,
    }


def chat(api_key: str, messages: list, temperature: float = 0.7,
         model: str = None, base_url: str = None, response_format=None,
         gemini_thinking: bool = False) -> tuple:
    """Returns (content, usage_dict) where usage_dict has prompt_tokens/completion_tokens/elapsed.
    gemini_thinking only applies when routed to Google - ignored for every other provider."""
    if _is_google(base_url):
        import gemini_client
        return gemini_client.chat(api_key, messages, temperature, model, base_url, response_format, gemini_thinking)
    model = model or DEFAULT_MODEL
    client = _client(api_key, base_url)
    started = time.time()
    print(f"[llm] request: model={model} messages={len(messages)} chars={sum(len(m['content']) for m in messages)}")
    kwargs = dict(model=model, messages=messages, max_tokens=MAX_TOKENS, temperature=temperature)
    if response_format:
        kwargs["response_format"] = response_format
    resp = client.chat.completions.create(**kwargs)
    elapsed = time.time() - started
    if not resp.choices or resp.choices[0].message is None or resp.choices[0].message.content is None:
        finish_reason = resp.choices[0].finish_reason if resp.choices else None
        raise RuntimeError(
            f"모델이 빈 응답을 반환했습니다 (model={model}, finish_reason={finish_reason}). "
            f"컨텍스트가 너무 길거나(max_tokens={MAX_TOKENS}), 이 서버/모델이 이 요청 형식을 "
            f"지원하지 않을 수 있습니다. raw={resp}"
        )
    content = resp.choices[0].message.content
    _log_usage(f"done: elapsed={elapsed:.1f}s", resp.usage)
    return content, _usage_dict(resp.usage, elapsed)


def chat_stream(api_key: str, messages: list, temperature: float = 0.7,
                model: str = None, base_url: str = None, response_format=None,
                gemini_thinking: bool = False):
    """Yields (accumulated_text, finish_reason, usage_dict).
    usage_dict is None for mid-stream yields and populated only on the final yield.
    gemini_thinking only applies when routed to Google - ignored for every other provider."""
    if _is_google(base_url):
        import gemini_client
        yield from gemini_client.chat_stream(api_key, messages, temperature, model, base_url, response_format, gemini_thinking)
        return
    model = model or DEFAULT_MODEL
    client = _client(api_key, base_url)
    started = time.time()
    print(f"[llm] stream: model={model} messages={len(messages)} chars={sum(len(m['content']) for m in messages)}")
    kwargs = dict(model=model, messages=messages, max_tokens=MAX_TOKENS, temperature=temperature, stream=True,
                  stream_options={"include_usage": True})
    if response_format:
        kwargs["response_format"] = response_format

    full = ""
    finish_reason = None
    usage = None
    try:
        stream_ctx = client.chat.completions.create(**kwargs)
    except Exception:
        # some local/OpenAI-compat servers reject stream_options entirely
        kwargs.pop("stream_options", None)
        stream_ctx = client.chat.completions.create(**kwargs)

    with stream_ctx as stream:
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = (choice.delta.content if choice.delta is not None else None) or ""
            if delta:
                full += delta
                yield full, None, None
            if choice.finish_reason:
                finish_reason = choice.finish_reason

    elapsed = time.time() - started
    _log_usage(f"stream done: elapsed={elapsed:.1f}s chars={len(full)} finish_reason={finish_reason}", usage)
    if not full:
        raise RuntimeError(
            f"모델이 빈 응답을 반환했습니다 (model={model}, finish_reason={finish_reason}). "
            f"컨텍스트가 너무 길거나(max_tokens={MAX_TOKENS}), 이 서버/모델이 스트리밍 요청 형식을 "
            f"지원하지 않을 수 있습니다."
        )
    yield full, finish_reason, _usage_dict(usage, elapsed)
