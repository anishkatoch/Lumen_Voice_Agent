"""
Tests for database repo functions — verifies correct Supabase calls are made.
All tests are sync (repos are sync functions).
"""
import pytest
from tests.conftest import TEST_USER_ID, TEST_CONV_ID


# ── conversation_repo ─────────────────────────────────────────────────

def test_create_conversation(mock_supabase):
    from app.conversation_repo import create_conversation
    result = create_conversation(TEST_USER_ID)
    assert result["id"] == TEST_CONV_ID
    assert result["user_id"] == TEST_USER_ID


def test_create_conversation_db_failure(mocker):
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.return_value.data = []
    mocker.patch("app.conversation_repo.get_supabase", return_value=mock_db)

    from app.conversation_repo import create_conversation
    with pytest.raises(RuntimeError, match="Failed to create conversation"):
        create_conversation(TEST_USER_ID)


def test_get_conversations(mock_supabase):
    from app.conversation_repo import get_conversations
    result = get_conversations(TEST_USER_ID)
    assert isinstance(result, list)
    assert result[0]["id"] == TEST_CONV_ID


def test_get_conversation_by_id_found(mock_supabase):
    from app.conversation_repo import get_conversation_by_id
    result = get_conversation_by_id(TEST_CONV_ID)
    assert result is not None
    assert result["id"] == TEST_CONV_ID


def test_get_conversation_by_id_not_found(mocker):
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    mocker.patch("app.conversation_repo.get_supabase", return_value=mock_db)

    from app.conversation_repo import get_conversation_by_id
    assert get_conversation_by_id("nonexistent") is None


# ── message_repo ──────────────────────────────────────────────────────

def test_save_message(mock_supabase):
    from app.message_repo import save_message
    result = save_message(TEST_CONV_ID, TEST_USER_ID, "user", "hello")
    assert result["role"] == "user"


def test_save_message_db_failure(mocker):
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.return_value.data = []
    mocker.patch("app.message_repo.get_supabase", return_value=mock_db)

    from app.message_repo import save_message
    with pytest.raises(RuntimeError, match="Failed to save message"):
        save_message(TEST_CONV_ID, TEST_USER_ID, "user", "hello")


def test_get_conversation_history(mock_supabase):
    from app.message_repo import get_conversation_history
    result = get_conversation_history(TEST_CONV_ID)
    assert isinstance(result, list)
    assert result[0]["role"] == "user"
    assert "created_at" in result[0]


def test_get_session_messages_strips_timestamp(mock_supabase):
    from app.message_repo import get_session_messages
    result = get_session_messages(TEST_CONV_ID)
    assert isinstance(result, list)
    assert set(result[0].keys()) == {"role", "content"}


# ── memory_repo ───────────────────────────────────────────────────────

def test_get_user_memory_empty(mock_supabase):
    from app.memory_repo import get_user_memory
    assert get_user_memory(TEST_USER_ID) is None


def test_get_user_memory_exists(mocker):
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"summary": "User likes short answers."}
    ]
    mocker.patch("app.memory_repo.get_supabase", return_value=mock_db)

    from app.memory_repo import get_user_memory
    assert get_user_memory(TEST_USER_ID) == "User likes short answers."


def test_upsert_user_memory(mocker):
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mocker.patch("app.memory_repo.get_supabase", return_value=mock_db)

    from app.memory_repo import upsert_user_memory
    upsert_user_memory(TEST_USER_ID, "Updated summary.")

    mock_db.table("user_memory").upsert.assert_called_once_with(
        {"user_id": TEST_USER_ID, "summary": "Updated summary."},
        on_conflict="user_id",
    )


# ── document_repo ─────────────────────────────────────────────────────

def test_save_chunk(mock_supabase):
    from app.document_repo import save_chunk
    result = save_chunk("faq.md", "Intro", "Some text", [0.1] * 1536)
    assert result["id"] == "doc-1"


def test_save_chunk_db_failure(mocker):
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.return_value.data = []
    mocker.patch("app.document_repo.get_supabase", return_value=mock_db)

    from app.document_repo import save_chunk
    with pytest.raises(RuntimeError, match="Failed to save chunk"):
        save_chunk("faq.md", "Intro", "Some text", [0.1] * 1536)


def test_vector_search(mocker):
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.rpc.return_value.execute.return_value.data = [
        {"id": "doc-1", "chunk_text": "Relevant text", "similarity": 0.95}
    ]
    mocker.patch("app.document_repo.get_supabase", return_value=mock_db)

    from app.document_repo import vector_search
    results = vector_search([0.1] * 1536, top_k=3)
    assert len(results) == 1
    assert results[0]["similarity"] == 0.95


def test_delete_chunks_by_file(mocker):
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mocker.patch("app.document_repo.get_supabase", return_value=mock_db)

    from app.document_repo import delete_chunks_by_file
    delete_chunks_by_file("old.md")
    mock_db.table("documents").delete().eq.assert_called_with("file_name", "old.md")
