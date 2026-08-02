"""Native Gemini client (google-genai SDK), used instead of the OpenAI-compat layer so we
can actually use Gemini's explicit context caching (cachedContents) - the OpenAI-compat
endpoint (llm_client.py) has no way to create/reference a cache, so a large repeated
prefix (system prompt + standing instructions + DB candidate list) gets re-billed as full
input tokens on every single call even if Gemini's automatic implicit caching doesn't kick
in for it. Explicit caching pins that prefix once and reuses it by reference.
"""
import hashlib
import time

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3.5-flash"
AVAILABLE_MODELS = ["gemini-3.5-pro", "gemini-3.5-flash", "gemini-3.1-pro", "gemini-3.1-flash"]

MAX_OUTPUT_TOKENS = 32768
REQUEST_TIMEOUT_MS = 300_000

# Explicit caching has a minimum content size (provider-enforced) below which cache
# creation just errors out - skip caching under this rough character threshold rather
# than round-tripping a doomed request. ~4 chars/token is a conservative estimate.
MIN_CACHE_CHARS = 6000
CACHE_TTL_SECONDS = 1800

_NON_CHAT_MODEL_MARKERS = (
    "embedding", "embed-", "aqa", "imagen", "veo", "tts", "audio", "vision-only",
    "image-generation", "moderation", "whisper", "dall-e", "clip",
)

# In-memory cache-handle registry, keyed by (api_key hash, model, system text hash).
# Gemini cache resources are billed/managed server-side by name; we just need to avoid
# re-creating one on every call within the same process lifetime.
_cache_registry = {}


def _client(api_key: str) -> genai.Client:
    if not api_key:
        raise ValueError("Google (Gemini) API 키가 필요합니다.")
    return genai.Client(api_key=api_key)


def _split_messages(messages: list):
    """Converts OpenAI-style {role, content} messages into (system_text, contents)
    for the native Gemini SDK. Gemini has no "system" role in contents - it's a
    separate system_instruction - and uses "model" instead of "assistant"."""
    system_parts = []
    contents = []
    for m in messages:
        role = m.get("role")
        text = m.get("content", "")
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=text)]))
        else:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
    return "\n\n".join(system_parts), contents


def _cache_key(api_key: str, model: str, system_text: str) -> str:
    digest = hashlib.sha256(f"{api_key}|{model}|{system_text}".encode("utf-8")).hexdigest()
    return digest


def _get_or_create_cache(client: genai.Client, model: str, api_key: str, system_text: str):
    """Returns a cached_content resource name to reuse, or None if the system text is too
    small to bother caching (or caching isn't supported/fails for this model)."""
    if len(system_text) < MIN_CACHE_CHARS:
        return None

    key = _cache_key(api_key, model, system_text)
    entry = _cache_registry.get(key)
    if entry and entry["expires_at"] > time.time():
        return entry["name"]

    try:
        cache = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                system_instruction=system_text,
                ttl=f"{CACHE_TTL_SECONDS}s",
            ),
        )
    except Exception as e:
        print(f"[gemini] cache create skipped: {e}")
        return None

    _cache_registry[key] = {"name": cache.name, "expires_at": time.time() + CACHE_TTL_SECONDS - 60}
    print(f"[gemini] created cache {cache.name} for model={model} chars={len(system_text)}")
    return cache.name


def _usage_dict(usage_metadata, elapsed: float) -> dict:
    if usage_metadata is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "elapsed": elapsed}
    return {
        "prompt_tokens": getattr(usage_metadata, "prompt_token_count", 0) or 0,
        "completion_tokens": getattr(usage_metadata, "candidates_token_count", 0) or 0,
        "elapsed": elapsed,
        "cached_tokens": getattr(usage_metadata, "cached_content_token_count", 0) or 0,
    }


def _log_usage(prefix: str, usage_metadata) -> None:
    if usage_metadata is None:
        return
    cached = getattr(usage_metadata, "cached_content_token_count", 0) or 0
    if cached:
        print(f"[gemini] {prefix} usage={usage_metadata} cache_hit_tokens={cached}")
    else:
        print(f"[gemini] {prefix} usage={usage_metadata}")


# Thinking is OFF by default: this app's tasks (tag grouping, JSON formatting, following a
# fixed style guide) rarely benefit from Gemini's extended reasoning, and it's pure latency/
# token cost otherwise. thinking_budget=-1 lets the model pick its own budget dynamically
# when a caller explicitly opts in; 0 disables it.
def _thinking_config(enabled: bool):
    return types.ThinkingConfig(thinking_budget=-1 if enabled else 0)


# Models that rejected thinking_budget=0 with "this model only works in thinking mode" -
# remembered per process so later calls to the same model skip straight to thinking-on
# instead of paying for a failed request + retry every single time.
_THINKING_REQUIRED_MODELS = set()


# This app's whole purpose is generating explicit adult-content danbooru tag prompts, so
# Gemini's default safety thresholds (which block sexual-content responses outright) have
# to be relaxed, or every multi-scene/NSFW-tag generation call comes back empty with no
# error - just 0 output tokens - which is exactly what silently broke scene generation.
_SAFETY_SETTINGS = [
    types.SafetySetting(category=category, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for category in (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
]


def _build_config(system_text, temperature, model, api_key, client, response_format, thinking_enabled=False):
    cache_name = _get_or_create_cache(client, model, api_key, system_text)
    if model in _THINKING_REQUIRED_MODELS:
        thinking_enabled = True
    config_kwargs = dict(
        temperature=temperature,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        thinking_config=_thinking_config(thinking_enabled),
        safety_settings=_SAFETY_SETTINGS,
    )
    if cache_name:
        config_kwargs["cached_content"] = cache_name
    else:
        config_kwargs["system_instruction"] = system_text or None
    if response_format and response_format.get("type") == "json_object":
        config_kwargs["response_mime_type"] = "application/json"
    return types.GenerateContentConfig(**config_kwargs)


def _blocked_error(resp_or_chunk, model: str) -> RuntimeError:
    feedback = getattr(resp_or_chunk, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None) if feedback else None
    candidates = getattr(resp_or_chunk, "candidates", None) or []
    finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    safety_ratings = getattr(candidates[0], "safety_ratings", None) if candidates else None
    return RuntimeError(
        f"Gemini가 빈 응답을 반환했습니다 (model={model}, block_reason={block_reason}, "
        f"finish_reason={finish_reason}). 세이프티 필터에 걸려 차단됐을 가능성이 높습니다"
        + (f" (safety_ratings={safety_ratings})" if safety_ratings else "") + "."
    )


def chat(api_key: str, messages: list, temperature: float = 0.7,
         model: str = None, base_url: str = None, response_format=None,
         thinking_enabled: bool = False) -> tuple:
    model = model or DEFAULT_MODEL
    client = _client(api_key)
    system_text, contents = _split_messages(messages)
    config = _build_config(system_text, temperature, model, api_key, client, response_format, thinking_enabled)

    started = time.time()
    print(f"[gemini] request: model={model} thinking={thinking_enabled} messages={len(messages)} chars={sum(len(m['content']) for m in messages)}")
    try:
        resp = client.models.generate_content(model=model, contents=contents, config=config)
    except Exception as e:
        # some models (e.g. reasoning-only previews) reject thinking_budget=0 outright and
        # REQUIRE thinking - omitting thinking_config entirely still resolves to a disabled/
        # zero budget for these, so the only working fallback is to force it on (dynamic
        # budget), not just remove the override.
        if "thinking" not in str(e).lower():
            raise
        _THINKING_REQUIRED_MODELS.add(model)
        config.thinking_config = _thinking_config(True)
        resp = client.models.generate_content(model=model, contents=contents, config=config)
    elapsed = time.time() - started
    _log_usage(f"done: elapsed={elapsed:.1f}s", resp.usage_metadata)
    if not resp.text:
        raise _blocked_error(resp, model)
    return resp.text, _usage_dict(resp.usage_metadata, elapsed)


def _stream_with_thinking_fallback(client, model, contents, config):
    """generate_content_stream() is a lazy generator - calling it makes no request at all;
    the actual API call (and any error, e.g. a model that rejects thinking_budget=0) only
    happens once you start iterating. So the "force thinking on" fallback has to wrap the
    FIRST iteration specifically, not the call that creates the generator."""
    stream = client.models.generate_content_stream(model=model, contents=contents, config=config)
    try:
        first_chunk = next(stream)
    except StopIteration:
        return
    except Exception as e:
        if "thinking" not in str(e).lower():
            raise
        # some models (e.g. reasoning-only previews) reject thinking_budget=0 outright and
        # REQUIRE thinking - omitting thinking_config entirely still resolves to a disabled/
        # zero budget for these, so the only working fallback is to force it on (dynamic
        # budget), not just remove the override.
        _THINKING_REQUIRED_MODELS.add(model)
        config.thinking_config = _thinking_config(True)
        stream = client.models.generate_content_stream(model=model, contents=contents, config=config)
        yield from stream
        return
    yield first_chunk
    yield from stream


def chat_stream(api_key: str, messages: list, temperature: float = 0.7,
                model: str = None, base_url: str = None, response_format=None,
                thinking_enabled: bool = False):
    """Yields (accumulated_text, finish_reason, usage_dict), matching llm_client.chat_stream's
    contract so core.py can treat both providers identically."""
    model = model or DEFAULT_MODEL
    client = _client(api_key)
    system_text, contents = _split_messages(messages)
    config = _build_config(system_text, temperature, model, api_key, client, response_format, thinking_enabled)

    started = time.time()
    print(f"[gemini] stream: model={model} thinking={thinking_enabled} messages={len(messages)} chars={sum(len(m['content']) for m in messages)}")

    full = ""
    finish_reason = None
    usage_metadata = None
    last_chunk = None
    for chunk in _stream_with_thinking_fallback(client, model, contents, config):
        last_chunk = chunk
        if getattr(chunk, "usage_metadata", None):
            usage_metadata = chunk.usage_metadata
        delta = chunk.text or ""
        if delta:
            full += delta
            yield full, None, None
        candidates = getattr(chunk, "candidates", None) or []
        if candidates and getattr(candidates[0], "finish_reason", None):
            finish_reason = str(candidates[0].finish_reason)

    elapsed = time.time() - started
    _log_usage(f"stream done: elapsed={elapsed:.1f}s chars={len(full)} finish_reason={finish_reason}", usage_metadata)
    if not full:
        raise _blocked_error(last_chunk, model)
    yield full, finish_reason, _usage_dict(usage_metadata, elapsed)


def list_models(api_key: str, base_url: str = None) -> list:
    client = _client(api_key)
    ids = []
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        model_id = (m.name or "").split("/")[-1]
        if not model_id or any(marker in model_id.lower() for marker in _NON_CHAT_MODEL_MARKERS):
            continue
        ids.append(model_id)
    return sorted(set(ids))
