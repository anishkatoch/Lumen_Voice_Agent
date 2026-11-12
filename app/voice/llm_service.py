import asyncio
import json
import re
from datetime import datetime
from openai import AsyncOpenAI, AsyncAzureOpenAI
from typing import AsyncGenerator
from app.core.config import (
    OPENAI_API_KEY,
    AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT,
    LLM_MODEL, LLM_TIMEOUT, LLM_MAX_TOKENS, LLM_TEMPERATURE, LLM_FALLBACK_MODEL,
    MEMORY_MODEL, MEMORY_MAX_TOKENS,
)

_openai: AsyncOpenAI | None = None
_azure_openai: AsyncAzureOpenAI | None = None


def _get_openai() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _openai


def _get_azure_openai() -> AsyncAzureOpenAI | None:
    """Azure OpenAI client - primary LLM source. None if not configured via env."""
    global _azure_openai
    if not AZURE_OPENAI_API_KEY or not AZURE_OPENAI_ENDPOINT:
        return None
    if _azure_openai is None:
        _azure_openai = AsyncAzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION,
        )
    return _azure_openai


def _augment_with_azure(
    client: AsyncOpenAI | None, provider: str, model: str
) -> list[tuple[AsyncOpenAI, str, str]]:
    """
    Given the single (client, provider, model) the configured admin provider
    resolves to, prepend Azure OpenAI as primary when that provider is the
    plain-"openai" default and Azure is configured via env vars. The original
    client/model becomes the fallback (using LLM_FALLBACK_MODEL), tried only
    if Azure is unconfigured or a call to it raises. groq/anthropic/ollama or
    a custom key/base_url (already baked into `client` by _get_llm_client)
    are untouched - no Azure involved.
    """
    if client is None:
        return []
    if provider != "openai":
        return [(client, provider, model)]

    azure = _get_azure_openai()
    if azure is None:
        return [(client, provider, model)]
    from app.admin.admin_config_service import get as get_config
    azure_deployment = get_config("azure_openai_deployment") or AZURE_OPENAI_DEPLOYMENT
    return [
        (azure, "azure_openai", azure_deployment),
        (client, provider, LLM_FALLBACK_MODEL),
    ]


def _get_llm_chain() -> list[tuple[AsyncOpenAI, str, str]]:
    """Ordered (client, provider_label, model) attempts for a chat-completions call."""
    client, provider, model = _get_llm_client()
    return _augment_with_azure(client, provider, model)


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3")


def _max_tokens_kwarg(model: str) -> dict:
    """gpt-5* / o1* / o3* reject `max_tokens` - they require `max_completion_tokens` instead."""
    if _is_reasoning_model(model):
        return {"max_completion_tokens": LLM_MAX_TOKENS}
    return {"max_tokens": LLM_MAX_TOKENS}


def _temperature_kwarg(model: str) -> dict:
    """gpt-5* / o1* / o3* only support the default temperature (1) - omit the param for them."""
    if _is_reasoning_model(model):
        return {}
    return {"temperature": LLM_TEMPERATURE}


def _get_llm_client() -> tuple[AsyncOpenAI | None, str, str]:
    """Return (client_or_None_for_anthropic, provider, model)."""
    from app.admin.admin_config_service import get as get_config
    provider  = get_config("llm_provider") or "openai"
    model     = get_config("llm_model")    or LLM_MODEL
    api_key   = get_config("llm_api_key")
    base_url  = get_config("llm_base_url")

    if provider == "anthropic":
        return None, provider, model
    if provider == "groq":
        import os
        key = api_key or os.environ.get("GROQ_API_KEY", "")
        return AsyncOpenAI(api_key=key, base_url="https://api.groq.com/openai/v1"), provider, model
    if provider == "ollama":
        base = base_url or "http://localhost:11434"
        return AsyncOpenAI(api_key="ollama", base_url=f"{base.rstrip('/')}/v1"), provider, model
    # Default: openai
    key = api_key or OPENAI_API_KEY
    return AsyncOpenAI(api_key=key), provider, model


async def test_llm_connection() -> dict:
    """Admin-panel "Test connection" check: sends one minimal completion through
    the exact same resolution chain a real call would use (including the
    Azure-primary-when-"openai" rule), and reports which provider actually
    answered, the model, latency, and the raw error from each attempt that
    failed before a working one was found (or before giving up)."""
    import time
    client, provider, model = _get_llm_client()
    attempts: list[dict] = []

    if provider == "anthropic":
        from app.admin.admin_config_service import get as get_config
        import httpx
        import os
        api_key = get_config("llm_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"ok": False, "provider": provider, "model": model,
                    "error": "No Anthropic API key configured (set it in Providers, or ANTHROPIC_API_KEY env var).",
                    "attempts": []}
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=LLM_TIMEOUT + 10) as hc:
                resp = await hc.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": model, "max_tokens": 5, "messages": [{"role": "user", "content": "ping"}]},
                )
            latency_ms = round((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                return {"ok": True, "provider": provider, "model": model, "latency_ms": latency_ms, "attempts": []}
            return {"ok": False, "provider": provider, "model": model, "latency_ms": latency_ms,
                    "error": f"HTTP {resp.status_code}: {resp.text[:300]}", "attempts": []}
        except Exception as e:
            return {"ok": False, "provider": provider, "model": model,
                    "error": str(e), "attempts": []}

    chain = _augment_with_azure(client, provider, model)
    if not chain:
        return {"ok": False, "provider": provider, "model": model, "error": "No client configured for this provider.", "attempts": []}

    for try_client, try_provider, try_model in chain:
        t0 = time.monotonic()
        try:
            await try_client.chat.completions.create(
                model=try_model,
                messages=[{"role": "user", "content": "ping"}],
                **_max_tokens_kwarg(try_model),
                timeout=LLM_TIMEOUT,
            )
            latency_ms = round((time.monotonic() - t0) * 1000)
            return {"ok": True, "provider": try_provider, "model": try_model, "latency_ms": latency_ms, "attempts": attempts}
        except Exception as e:
            latency_ms = round((time.monotonic() - t0) * 1000)
            attempts.append({"provider": try_provider, "model": try_model, "latency_ms": latency_ms, "error": str(e)})

    last = attempts[-1] if attempts else {}
    return {"ok": False, "provider": last.get("provider", provider), "model": last.get("model", model),
            "error": last.get("error", "Unknown error"), "attempts": attempts}


async def _anthropic_token_stream(
    system: str, messages: list[dict], model: str, api_key: str
) -> AsyncGenerator[str, None]:
    """Stream tokens from Anthropic Messages API via httpx SSE."""
    import httpx
    import os
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": LLM_TEMPERATURE,
        "system": system,
        "messages": messages,
        "stream": True,
    }
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT + 10) as client:
            async with client.stream(
                "POST", "https://api.anthropic.com/v1/messages",
                headers=headers, json=body,
            ) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    print(f"[Anthropic] HTTP {resp.status_code}: {text[:200]}")
                    yield "I encountered an error with the AI provider. Please try again."
                    return
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            obj = json.loads(line[6:])
                            if obj.get("type") == "content_block_delta":
                                token = obj.get("delta", {}).get("text", "")
                                if token:
                                    yield token
                        except Exception:
                            pass
    except Exception as e:
        print(f"[Anthropic] Stream error: {e}")
        yield "I encountered an error. Please try again."


async def generate_response(
    user_message: str,
    session_history: list[dict],
    rag_context: str,
    long_term_memory: str | None,
) -> str:
    client = _get_openai()

    system_parts = [
        "You are a helpful voice AI assistant. "
        "Respond concisely - your answers will be spoken aloud. "
        "Avoid markdown, bullet points, or formatting. "
        "Speak naturally as if in a conversation.\n"
        "Follow these rules:\n"
        "1. For questions about the current conversation, the user, or general chat - answer naturally using the conversation history.\n"
        "2. For questions about specific topics, facts, or information - ONLY use the knowledge base context provided below. "
        "If the topic is not in the knowledge base, say: 'I don't have information about that in my knowledge base.' "
        "Do not use your general training knowledge to answer factual questions."
    ]

    if long_term_memory:
        system_parts.append(f"\nWhat you know about this user:\n{long_term_memory}")

    if rag_context:
        system_parts.append(f"\n{rag_context}")
    else:
        system_parts.append("\nNo relevant documents found in the knowledge base for this query.")

    messages = [{"role": "system", "content": "\n".join(system_parts)}]
    messages.extend(session_history)
    messages.append({"role": "user", "content": user_message})

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                max_tokens=LLM_MAX_TOKENS,
                temperature=LLM_TEMPERATURE,
            ),
            timeout=LLM_TIMEOUT,
        )
        return response.choices[0].message.content.strip()
    except asyncio.TimeoutError:
        return "I'm sorry, I took too long to think. Could you ask again?"
    except Exception as e:
        print(f"[LLM] Error: {e}")
        return "I encountered an error. Please try again."


_DEFAULT_SYSTEM = (
    "# Identity & Role\n"
    "You are a helpful AI voice agent. You assist callers with their questions, tasks, and requests. "
    "Be empathetic, professional, and concise. Confirm important details before finalizing anything. "
    "Respond naturally for a voice conversation - no bullet points or markdown.\n"
    "\n"
    "# Current Date & Time\n"
    "Today is {{current_time}}. Use this as the only source of truth for \"today\" - never your training data.\n"
    "\n"
    "## Voice & Persona\n"
    "\n"
    "### Personality\n"
    "**Friendly & Patient:** Sound warm, patient, and knowledgeable.\n"
    "**Concise & Direct:** **This is a strict rule.** Always get straight to the point. Avoid filler words and long explanations. "
    "Your goal is to resolve the query in as few turns as possible.\n"
    "**Natural & Conversational:** Use a human-like, conversational tone (e.g., \"I'm,\" \"don't,\" \"it's\").\n"
    "**Confident:** Speak with confidence, but if you don't know something, say \"Hmm, let me check on that for you.\"\n"
    "\n"
    "### Speech Characteristics\n"
    "Use contractions (I'm, we'll, don't).\n"
    "**Keep responses short,** ideally under 30 words.\n"
    "Ask only one question at a time.\n"
    "\n"
    "### Tone\n"
    "Friendly\n"
    "Professional\n"
    "Clear and concise\n"
    "Confident, not pushy\n"
    "\n"
    "# Purpose\n"
    "Follow these rules:\n"
    "1. For questions about the current conversation, the user, or general chat - answer naturally using the conversation history.\n"
    "2. For questions about specific topics, facts, or information - ONLY use the knowledge base context provided below. "
    "If the topic is not in the knowledge base, say: 'I don't have information about that in my knowledge base.' "
    "Do not use your general training knowledge to answer factual questions.\n"
    "\n"
    "# Knowledge Base"
)


async def generate_response_stream(
    user_message: str,
    session_history: list[dict],
    rag_context: str,
    long_term_memory: str | None,
    system_prompt_override: str | None = None,
    kb_instructions: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream LLM response sentence by sentence using the configured provider."""
    client, provider, model = _get_llm_client()

    system_str = build_system_prompt(system_prompt_override, long_term_memory, rag_context, kb_instructions)
    conv_messages = list(session_history) + [{"role": "user", "content": user_message}]

    buffer = ""
    sentence_end = re.compile(r'(?<=[.!?;])\s+|(?<=,)\s+(?=\w{4})')

    async def _flush_buffer(token_gen):
        nonlocal buffer
        async for token in token_gen:
            buffer += token
            parts = sentence_end.split(buffer)
            for sentence in parts[:-1]:
                s = sentence.strip()
                if s:
                    yield s
            buffer = parts[-1]
        if buffer.strip():
            yield buffer.strip()

    if provider == "anthropic":
        try:
            from app.admin.admin_config_service import get as get_config
            api_key = get_config("llm_api_key")
            token_gen = _anthropic_token_stream(system_str, conv_messages, model, api_key)
            async for sentence in _flush_buffer(token_gen):
                yield sentence
        except asyncio.TimeoutError:
            yield "I'm sorry, I took too long to think. Could you ask again?"
        except Exception as e:
            print(f"[LLM] Stream error (anthropic): {e}")
            yield "I encountered an error. Please try again."
        return

    chain = _augment_with_azure(client, provider, model)
    if not chain:
        yield "I encountered an error. Please try again."
        return

    last_err: Exception | None = None
    for idx, (try_client, try_provider, try_model) in enumerate(chain):
        is_last = idx == len(chain) - 1
        buffer = ""
        try:
            stream = await try_client.chat.completions.create(
                model=try_model,
                messages=[{"role": "system", "content": system_str}] + conv_messages,
                **_max_tokens_kwarg(try_model),
                **_temperature_kwarg(try_model),
                stream=True,
                timeout=LLM_TIMEOUT,
            )

            async def _openai_tokens(s=stream):
                async for chunk in s:
                    if not chunk.choices:
                        continue
                    token = chunk.choices[0].delta.content or ""
                    if token:
                        yield token

            async for sentence in _flush_buffer(_openai_tokens()):
                yield sentence
            return
        except asyncio.TimeoutError as e:
            last_err = e
            print(f"[LLM] {try_provider} ({try_model}) timed out" + ("" if is_last else " - falling back"))
        except Exception as e:
            last_err = e
            print(f"[LLM] {try_provider} ({try_model}) stream error: {e}" + ("" if is_last else " - falling back"))

    print(f"[LLM] All providers failed: {last_err}")
    if isinstance(last_err, asyncio.TimeoutError):
        yield "I'm sorry, I took too long to think. Could you ask again?"
    else:
        yield "I encountered an error. Please try again."


FILLER_PHRASES_BY_CATEGORY: dict[str, list[str]] = {
    # The caller just asked something and is waiting on an answer.
    "question": [
        "Let me check that for you.",
        "One moment, I'll look that up.",
        "Great question - let me pull that up.",
        "Good question, hold on.",
    ],
    # The caller just gave information or confirmed something (an answer,
    # a number, a "yes"/"no") - "let me check" reads oddly there since
    # there's nothing to look up, it's just been handed to us.
    "info": [
        "Got it.",
        "Thanks, noted.",
        "Okay, got that.",
        "Alright, noted.",
    ],
    # Anything else (small talk, the start of a turn, nothing classifiable).
    "default": [
        "One moment.",
        "Just a second.",
        "Give me a moment.",
        "Okay.",
    ],
}
FILLER_PHRASES = FILLER_PHRASES_BY_CATEGORY["default"]  # back-compat alias for any external import

_FILLER_CATEGORIES = ("question", "info", "default")
_QUESTION_STARTERS = (
    "what", "why", "how", "when", "where", "who", "which",
    "can you", "could you", "would you", "do you", "does", "is it", "are you", "will you",
)
_INFO_WORDS = {"yes", "no", "yeah", "yep", "nope", "correct", "right", "sure", "ok", "okay"}


def classify_filler_category(user_message: str) -> str:
    """Cheap, no-LLM classification of the caller's last message, used to
    pick a filler that actually fits the situation instead of a random one -
    a filler dropped in front of a confirmation ("Got it.") reads very
    differently from one dropped in front of the answer to a question
    ("Let me check that for you."). This runs before the real reply exists
    (that's the whole point of a filler - it covers the wait for it), so it
    can only look at the caller's own words, never the content of the reply
    it's covering for."""
    text = (user_message or "").strip().lower()
    if not text:
        return "default"
    if text.endswith("?") or text.startswith(_QUESTION_STARTERS):
        return "question"
    # First word only, punctuation stripped - "Yes, speaking." should match
    # "yes" without "yesterday is fine" also matching it.
    first_word = re.split(r"[\s,.!?]+", text, maxsplit=1)[0]
    if first_word in _INFO_WORDS or re.search(r"\d", text):
        return "info"
    return "default"


def get_filler_phrases(agent: dict | None, category: str = "default") -> list[str]:
    """Filler phrases for `category` (from classify_filler_category) -
    agent's own filler_phrases if configured for that category, else the
    built-in FILLER_PHRASES_BY_CATEGORY list. Spoken while a slow RAG/LLM
    call is in flight, so the caller doesn't hear dead air.

    An agent's filler_phrases is still just one phrase per line, but a line
    may be prefixed "question:", "info:", or "default:" to scope it to one
    category; an unprefixed line is treated as "default" and used as a
    catch-all for any category with no lines of its own - so an agent with
    a plain flat list (no prefixes at all) behaves exactly as before,
    situational selection is opt-in via prefixes, not a breaking change."""
    if category not in _FILLER_CATEGORIES:
        category = "default"
    if agent:
        raw = (agent.get("filler_phrases") or "").strip()
        if raw:
            buckets: dict[str, list[str]] = {"question": [], "info": [], "default": []}
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                for key in _FILLER_CATEGORIES:
                    prefix = f"{key}:"
                    if line.lower().startswith(prefix):
                        phrase = line[len(prefix):].strip()
                        if phrase:
                            buckets[key].append(phrase)
                        break
                else:
                    buckets["default"].append(line)
            if buckets[category]:
                return buckets[category]
            if buckets["default"]:
                return buckets["default"]
    return FILLER_PHRASES_BY_CATEGORY.get(category) or FILLER_PHRASES_BY_CATEGORY["default"]


def pick_filler_phrase(agent: dict | None, category: str, last_used: str | None = None) -> str:
    """random.choice over get_filler_phrases(agent, category), but excludes
    `last_used` (the filler most recently spoken in this same call) when
    there's another option to pick instead. Without this, a short filler
    list makes the exact same word repeat turn after turn by sheer chance -
    especially likely here since most agents only have 2-4 words per
    category - which reads as a stuck/broken agent rather than a natural
    one. Falls back to the full list (so `last_used` can still be picked
    again) only when it's the single option available for this category."""
    import random
    options = get_filler_phrases(agent, category)
    if last_used and len(options) > 1:
        narrowed = [o for o in options if o != last_used]
        if narrowed:
            options = narrowed
    return random.choice(options)


def build_system_prompt(
    system_prompt_override: str | None,
    long_term_memory: str | None,
    rag_context: str,
    kb_instructions: str | None,
    has_kb: bool = True,
) -> str:
    """has_kb: whether this agent/session has any knowledge base documents
    configured at all. When False, the "no relevant documents found" filler
    is skipped entirely - that line only makes sense as a guardrail when a
    KB exists but this particular query didn't match anything in it, not as
    boilerplate on every single turn of an agent with no KB configured."""
    base = system_prompt_override if system_prompt_override else _DEFAULT_SYSTEM
    if "{{current_time}}" in base:
        base = base.replace("{{current_time}}", datetime.now().strftime("%A, %B %d, %Y %I:%M %p"))
    parts = [base]
    if long_term_memory:
        parts.append(f"\nWhat you know about this user:\n{long_term_memory}")
    if rag_context:
        parts.append(f"\n{rag_context}")
        if kb_instructions:
            parts.append(f"\nKnowledge base instructions: {kb_instructions}")
    elif has_kb:
        parts.append("\nNo relevant documents found in the knowledge base for this query.")
        if kb_instructions:
            parts.append(f"\nKnowledge base instructions: {kb_instructions}")
    return "\n".join(parts)


def build_openai_tool(fn: dict) -> dict:
    """Convert a saved agent function dict into an OpenAI tool definition."""
    properties: dict = {}
    required: list[str] = []
    for p in fn.get("parameters") or []:
        ptype = p.get("type", "string")
        if ptype not in ("string", "number", "integer", "boolean", "array", "object"):
            ptype = "string"
        properties[p["name"]] = {"type": ptype, "description": p.get("description", "")}
        required.append(p["name"])
    return {
        "type": "function",
        "function": {
            "name": fn["name"],
            "description": fn.get("description") or f"Call {fn['name']}",
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


async def detect_tool_call(
    conv_messages: list[dict],
    system: str,
    tools: list[dict],
) -> tuple[str | None, str | None, dict, str | None]:
    """
    Single non-streaming LLM call with tool definitions.
    Returns:
      (text, None, {}, None)          - LLM replied with text, no tool needed
      (None, tool_name, args, call_id) - LLM wants to call a tool
    """
    client, provider, model = _get_llm_client()
    if provider == "anthropic" or client is None:
        # Anthropic tool calling has a different API - fall back to plain text
        return "", None, {}, None

    chain = _augment_with_azure(client, provider, model)
    if not chain:
        return "", None, {}, None

    last_err: Exception | None = None
    for try_client, try_provider, try_model in chain:
        try:
            response = await asyncio.wait_for(
                try_client.chat.completions.create(
                    model=try_model,
                    messages=[{"role": "system", "content": system}] + conv_messages,
                    tools=tools,
                    tool_choice="auto",
                    **_max_tokens_kwarg(try_model),
                    **_temperature_kwarg(try_model),
                    stream=False,
                ),
                timeout=LLM_TIMEOUT,
            )
            choice = response.choices[0]
            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                tc = choice.message.tool_calls[0]
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                return None, tc.function.name, args, tc.id
            return choice.message.content or "", None, {}, None
        except Exception as e:
            last_err = e
            print(f"[LLM Tool] {try_provider} ({try_model}) detect error: {e} - falling back")

    print(f"[LLM Tool] All providers failed: {last_err}")
    return "", None, {}, None


def tool_steps_to_messages(steps: list[tuple[str, str, dict, str]]) -> list[dict]:
    """Render already-executed tool steps [(name, call_id, args, result), ...]
    as OpenAI chat messages, so a follow-up detect_tool_call can let the model
    chain another tool in the same caller turn (e.g. check_availability and
    then reschedule_meeting). Without this, the cascade pipelines were limited
    to exactly one tool call per turn, and a model that said 'I'll move it
    now' after an availability check silently never did (found live
    2026-11-12)."""
    msgs: list[dict] = []
    for name, call_id, args, result in steps:
        msgs.append({
            "role": "assistant",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }],
        })
        msgs.append({"role": "tool", "tool_call_id": call_id, "content": result})
    return msgs


async def stream_tool_result_response(
    conv_messages: list[dict],
    system: str,
    tool_name: str,
    call_id: str,
    tool_args: dict,
    tool_result: str,
) -> AsyncGenerator[str, None]:
    """
    Second LLM call after a webhook returned its result.
    Streams the final spoken reply sentence by sentence.
    """
    client, provider, model = _get_llm_client()

    messages_with_result = (
        [{"role": "system", "content": system}]
        + conv_messages
        + [
            {
                "role": "assistant",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(tool_args)},
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "content": tool_result},
        ]
    )

    sentence_end = re.compile(r'(?<=[.!?;])\s+|(?<=,)\s+(?=\w{4})')

    if provider == "anthropic" or client is None:
        yield f"Based on the information I retrieved: {tool_result}"
        return

    chain = _augment_with_azure(client, provider, model)
    if not chain:
        yield "I found some information but had trouble reading it. Please try again."
        return

    last_err: Exception | None = None
    for try_client, try_provider, try_model in chain:
        buffer = ""
        try:
            stream = await try_client.chat.completions.create(
                model=try_model,
                messages=messages_with_result,
                **_max_tokens_kwarg(try_model),
                **_temperature_kwarg(try_model),
                stream=True,
                timeout=LLM_TIMEOUT,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                token = chunk.choices[0].delta.content or ""
                if not token:
                    continue
                buffer += token
                parts = sentence_end.split(buffer)
                for sentence in parts[:-1]:
                    s = sentence.strip()
                    if s:
                        yield s
                buffer = parts[-1]
            if buffer.strip():
                yield buffer.strip()
            return
        except Exception as e:
            last_err = e
            print(f"[LLM Tool] {try_provider} ({try_model}) stream_result error: {e} - falling back")

    print(f"[LLM Tool] All providers failed: {last_err}")
    yield "I found some information but had trouble reading it. Please try again."


async def stream_text_as_sentences(text: str) -> AsyncGenerator[str, None]:
    """Split a pre-generated text block into spoken sentences."""
    sentence_end = re.compile(r'(?<=[.!?;])\s+')
    parts = sentence_end.split(text.strip())
    for part in parts:
        s = part.strip()
        if s:
            yield s


async def _summarize_with_fallback(prompt: str, max_tokens: int) -> str | None:
    """Non-streaming chat completion using the same Azure-first fallback
    chain normal conversation replies use (_augment_with_azure/_get_llm_chain)
    - summaries must not silently depend on a plain OpenAI key being valid
    when Azure is the account's actual configured provider. Returns None if
    every attempt in the chain fails."""
    client, provider, model = _get_llm_client()
    chain = _augment_with_azure(client, provider, model)
    if not chain:
        chain = [(_get_openai(), "openai", MEMORY_MODEL)]
    for try_client, try_provider, _try_model in chain:
        # azure_openai already carries the admin-overridden deployment as
        # _try_model (see _augment_with_azure) - reuse it instead of
        # re-deriving the env default here.
        use_model = _try_model if try_provider == "azure_openai" else MEMORY_MODEL
        try:
            response = await try_client.chat.completions.create(
                model=use_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[LLM] Summary error ({try_provider}): {e}")
            continue
    return None


async def generate_call_summary(messages: list[dict]) -> str:
    """One-paragraph summary of a single finished call, for Call Analytics.
    Distinct from generate_memory_summary below, which is a rolling per-user
    memory paragraph carried across calls - this is per-call and discarded
    once written, never fed back into future prompts."""
    if not messages:
        return ""
    history_text = "\n".join(
        f"{m['role'].capitalize()}: {m['content']}" for m in messages
    )
    prompt = (
        "Summarize this phone call in a concise paragraph (max 120 words): "
        "what the caller wanted, what was discussed or resolved, and any "
        "follow-up action needed. Write it for someone who wasn't on the "
        "call and needs the gist quickly.\n\n"
        f"Transcript:\n{history_text}"
    )
    return await _summarize_with_fallback(prompt, MEMORY_MAX_TOKENS) or ""


async def generate_memory_summary(
    old_summary: str | None,
    new_messages: list[dict],
) -> str:
    history_text = "\n".join(
        f"{m['role'].capitalize()}: {m['content']}" for m in new_messages
    )

    prompt_parts = ["Summarize what you now know about this user based on:"]
    if old_summary:
        prompt_parts.append(f"Previous summary:\n{old_summary}")
    prompt_parts.append(f"New conversation:\n{history_text}")
    prompt_parts.append(
        "\nWrite a concise paragraph (max 200 words) capturing key facts, "
        "preferences, and past issues. Merge old and new info - no repetition."
    )

    result = await _summarize_with_fallback("\n\n".join(prompt_parts), MEMORY_MAX_TOKENS)
    return result if result is not None else (old_summary or "")
