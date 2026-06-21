import asyncio
from openai import AsyncOpenAI
from app.config import (
    OPENAI_API_KEY,
    LLM_MODEL, LLM_TIMEOUT, LLM_MAX_TOKENS, LLM_TEMPERATURE,
    MEMORY_MODEL, MEMORY_MAX_TOKENS,
)

_openai: AsyncOpenAI | None = None


def _get_openai() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _openai


async def generate_response(
    user_message: str,
    session_history: list[dict],
    rag_context: str,
    long_term_memory: str | None,
) -> str:
    client = _get_openai()

    system_parts = [
        "You are a helpful voice AI assistant. "
        "Respond concisely — your answers will be spoken aloud. "
        "Avoid markdown, bullet points, or formatting. "
        "Speak naturally as if in a conversation.\n"
        "Follow these rules:\n"
        "1. For questions about the current conversation, the user, or general chat — answer naturally using the conversation history.\n"
        "2. For questions about specific topics, facts, or information — ONLY use the knowledge base context provided below. "
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


async def generate_memory_summary(
    old_summary: str | None,
    new_messages: list[dict],
) -> str:
    client = _get_openai()

    history_text = "\n".join(
        f"{m['role'].capitalize()}: {m['content']}" for m in new_messages
    )

    prompt_parts = ["Summarize what you now know about this user based on:"]
    if old_summary:
        prompt_parts.append(f"Previous summary:\n{old_summary}")
    prompt_parts.append(f"New conversation:\n{history_text}")
    prompt_parts.append(
        "\nWrite a concise paragraph (max 200 words) capturing key facts, "
        "preferences, and past issues. Merge old and new info — no repetition."
    )

    try:
        response = await client.chat.completions.create(
            model=MEMORY_MODEL,
            messages=[{"role": "user", "content": "\n\n".join(prompt_parts)}],
            max_tokens=MEMORY_MAX_TOKENS,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[LLM] Memory summary error: {e}")
        return old_summary or ""
