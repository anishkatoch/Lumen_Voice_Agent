"""Shared embedding helper - Azure OpenAI first (if configured), plain
OpenAI as fallback only if Azure is unconfigured or the call fails. Mirrors
the Azure-first pattern app/voice/llm_service.py already uses for chat
completions, applied to embeddings - every embedding call site in the app
(agent KB, user KB, document ingestion) should go through this instead of
instantiating its own OpenAI client, so a dead plain OPENAI_API_KEY can't
break RAG the way it did before this existed."""
from openai import OpenAI, AzureOpenAI

from app.core.config import (
    OPENAI_API_KEY, RAG_EMBEDDING_MODEL,
    AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
)

_openai = OpenAI(api_key=OPENAI_API_KEY)
_azure_openai = (
    AzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    if AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT
    else None
)


def embed(inputs: list[str]) -> list[list[float]]:
    """Embed one or more strings in a single call, preserving input order."""
    if _azure_openai is not None:
        try:
            response = _azure_openai.embeddings.create(model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT, input=inputs)
            return [item.embedding for item in response.data]
        except Exception:
            pass
    response = _openai.embeddings.create(model=RAG_EMBEDDING_MODEL, input=inputs)
    return [item.embedding for item in response.data]
