"""
Unit tests for LLM, RAG, STT, TTS, VAD, and doc processor services.
All external API calls are mocked.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.asyncio

from tests.conftest import TEST_USER_ID, TEST_CONV_ID


# ── llm_service ───────────────────────────────────────────────────────

async def test_generate_response(mocker):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "  Hello back.  "
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    mocker.patch("app.llm_service._get_openai", return_value=mock_client)

    from app.llm_service import generate_response
    reply = await generate_response("Hi", [], "Some context.", "User prefers short answers.")
    assert reply == "Hello back."


async def test_generate_response_timeout(mocker):
    import asyncio
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=asyncio.TimeoutError)
    mocker.patch("app.llm_service._get_openai", return_value=mock_client)

    from app.llm_service import generate_response
    reply = await generate_response("Hi", [], "", None)
    assert "too long" in reply.lower()


async def test_generate_response_no_rag_injects_fallback(mocker):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "No docs reply."
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    mocker.patch("app.llm_service._get_openai", return_value=mock_client)

    from app.llm_service import generate_response
    await generate_response("What is X?", [], "", None)

    system_msg = mock_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "No relevant documents" in system_msg


async def test_generate_response_with_memory(mocker):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Reply."
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    mocker.patch("app.llm_service._get_openai", return_value=mock_client)

    from app.llm_service import generate_response
    await generate_response("Hi", [], "", "User is called Alice.")

    system_msg = mock_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "Alice" in system_msg


async def test_generate_memory_summary(mocker):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Summary of user."
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    mocker.patch("app.llm_service._get_openai", return_value=mock_client)

    from app.llm_service import generate_memory_summary
    result = await generate_memory_summary("Old summary.", [{"role": "user", "content": "I like Python."}])
    assert result == "Summary of user."


async def test_generate_memory_summary_error_returns_old(mocker):
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API down"))
    mocker.patch("app.llm_service._get_openai", return_value=mock_client)

    from app.llm_service import generate_memory_summary
    result = await generate_memory_summary("Previous summary.", [])
    assert result == "Previous summary."


async def test_generate_memory_summary_no_old_summary_returns_empty(mocker):
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API down"))
    mocker.patch("app.llm_service._get_openai", return_value=mock_client)

    from app.llm_service import generate_memory_summary
    result = await generate_memory_summary(None, [])
    assert result == ""


# ── rag_service ───────────────────────────────────────────────────────

async def test_embed_text(mocker):
    mock_response = MagicMock()
    mock_response.data[0].embedding = [0.1] * 1536
    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(return_value=mock_response)
    mocker.patch("app.rag_service._get_openai", return_value=mock_client)

    from app.rag_service import embed_text
    result = await embed_text("test query")
    assert len(result) == 1536


async def test_retrieve_context(mocker):
    mocker.patch("app.rag_service.embed_text", return_value=[0.1] * 1536)
    mocker.patch("app.rag_service.vector_search", return_value=[
        {"section_title": "FAQ", "chunk_text": "Answer here.", "similarity": 0.9}
    ])

    from app.rag_service import retrieve_context
    results = await retrieve_context("What is X?")
    assert len(results) == 1
    assert results[0]["section_title"] == "FAQ"


async def test_retrieve_context_returns_empty_on_error(mocker):
    mocker.patch("app.rag_service.embed_text", side_effect=Exception("OpenAI down"))

    from app.rag_service import retrieve_context
    results = await retrieve_context("What is X?")
    assert results == []


def test_format_rag_context_empty():
    from app.rag_service import format_rag_context
    assert format_rag_context([]) == ""


def test_format_rag_context_with_chunks():
    from app.rag_service import format_rag_context
    chunks = [
        {"section_title": "Intro", "chunk_text": "Welcome."},
        {"section_title": "FAQ", "chunk_text": "Answers here."},
    ]
    result = format_rag_context(chunks)
    assert "Relevant knowledge base context:" in result
    assert "[1] Intro" in result
    assert "[2] FAQ" in result


# ── deepgram_service ──────────────────────────────────────────────────

async def test_transcribe_audio_success(mocker):
    mock_alt = MagicMock()
    mock_alt.transcript = "  hello world  "
    mock_response = MagicMock()
    mock_response.results.channels[0].alternatives = [mock_alt]
    mocker.patch("asyncio.wait_for", return_value=mock_response)

    from app.deepgram_service import transcribe_audio
    result = await transcribe_audio(b"fake-wav-data")
    assert result == "hello world"


async def test_transcribe_audio_empty_transcript(mocker):
    mock_alt = MagicMock()
    mock_alt.transcript = "   "
    mock_response = MagicMock()
    mock_response.results.channels[0].alternatives = [mock_alt]
    mocker.patch("asyncio.wait_for", return_value=mock_response)

    from app.deepgram_service import transcribe_audio
    result = await transcribe_audio(b"silence")
    assert result is None


async def test_transcribe_audio_timeout(mocker):
    import asyncio
    mocker.patch("asyncio.wait_for", side_effect=asyncio.TimeoutError)

    from app.deepgram_service import transcribe_audio
    result = await transcribe_audio(b"slow-audio")
    assert result is None


# ── vad_service ───────────────────────────────────────────────────────

def test_build_wav_bytes_valid():
    from app.vad_service import build_wav_bytes
    pcm = b"\x00\x00" * 16000   # 1 second silence at 16kHz mono int16
    wav = build_wav_bytes(pcm, sample_rate=16000)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"


def test_build_wav_bytes_odd_length():
    from app.vad_service import build_wav_bytes
    pcm = b"\x00" * 101   # odd → trimmed to 100 bytes
    wav = build_wav_bytes(pcm)
    assert wav[:4] == b"RIFF"


# ── md_extractor ──────────────────────────────────────────────────────

def test_extract_chunks_with_headings():
    from app.md_extractor import extract_chunks
    md = "# Section One\nSome text here.\n## Section Two\nMore text."
    chunks = extract_chunks(md)
    assert len(chunks) >= 1
    assert any("Section" in c["section_title"] for c in chunks)


def test_extract_chunks_empty_doc():
    from app.md_extractor import extract_chunks
    assert extract_chunks("") == []


def test_extract_chunks_no_headings():
    from app.md_extractor import extract_chunks
    chunks = extract_chunks("Just a paragraph with no headings at all.")
    assert len(chunks) == 1
    assert chunks[0]["section_title"] == "Introduction"


# ── doc_processor ─────────────────────────────────────────────────────

async def test_ingest_document(mock_supabase, mocker):
    mock_supabase.storage.from_.return_value.download.return_value = b"# Guide\nHelpful content."
    mocker.patch("app.doc_processor.embed_text", return_value=[0.1] * 1536)

    from app.doc_processor import ingest_document
    count = await ingest_document("guide.md")
    assert count >= 1


async def test_ingest_document_file_not_found(mock_supabase):
    mock_supabase.storage.from_.return_value.download.return_value = None

    from app.doc_processor import ingest_document
    with pytest.raises(ValueError, match="File not found"):
        await ingest_document("missing.md")


async def test_ingest_document_empty_file(mock_supabase, mocker):
    # Non-empty bytes (truthy) but markdown with no extractable text
    mock_supabase.storage.from_.return_value.download.return_value = b"   \n   "
    mocker.patch("app.doc_processor.embed_text", return_value=[0.1] * 1536)

    from app.doc_processor import ingest_document
    with pytest.raises(ValueError, match="No content extracted"):
        await ingest_document("empty.md")
