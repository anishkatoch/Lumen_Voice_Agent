"""
Tests for the on-demand KB-search eligibility/scoping helpers in
app/telephony/shared.py (filter_kb_documents_by_instructions,
get_eligible_kb_documents, resolve_kb_search_doc_ids). These had no direct
test coverage before, despite being the exact gating logic that makes phone
calls only search KB docs kb_instructions actually names - and, after fixing
the browser "test this agent" feature to use the same on-demand tool instead
of eagerly pulling the agent's KB every turn (see app/routers/websockets.py),
the browser test path now depends on this logic behaving correctly too.
"""
import pytest

pytestmark = pytest.mark.asyncio

DOC_A = {"id": "doc-a", "display_name": "Employee Handbook.pdf"}
DOC_B = {"id": "doc-b", "display_name": "Referral Policy.pdf"}


class TestFilterKbDocumentsByInstructions:
    def test_empty_instructions_means_no_documents_eligible(self):
        from app.telephony.shared import filter_kb_documents_by_instructions
        assert filter_kb_documents_by_instructions("", [DOC_A]) == []
        assert filter_kb_documents_by_instructions("   ", [DOC_A, DOC_B]) == []

    def test_single_document_eligible_with_any_nonempty_instructions(self):
        """With only one document selected, a generic instruction like "use
        the attached documents" unambiguously refers to it - it doesn't need
        to be named."""
        from app.telephony.shared import filter_kb_documents_by_instructions
        result = filter_kb_documents_by_instructions("Use the attached documents as your source of truth.", [DOC_A])
        assert result == [DOC_A]

    def test_multiple_documents_only_named_ones_eligible(self):
        from app.telephony.shared import filter_kb_documents_by_instructions
        result = filter_kb_documents_by_instructions(
            "Answer HR questions using the Employee Handbook.", [DOC_A, DOC_B]
        )
        assert result == [DOC_A]

    def test_multiple_documents_none_named_means_none_eligible(self):
        """A second selected document must never be searched just because
        *some* kb_instructions exist, if it's never actually named."""
        from app.telephony.shared import filter_kb_documents_by_instructions
        result = filter_kb_documents_by_instructions(
            "Be friendly and helpful on every call.", [DOC_A, DOC_B]
        )
        assert result == []


class TestGetEligibleKbDocuments:
    async def test_no_selected_doc_ids_returns_empty(self, mocker):
        from app.telephony.shared import get_eligible_kb_documents
        mocker.patch("app.kb.user_kb_repo.get_user_documents", return_value=[DOC_A, DOC_B])
        agent = {"selected_kb_doc_ids": [], "kb_instructions": "Use the handbook."}
        assert await get_eligible_kb_documents(agent, "user-1") == []

    async def test_no_kb_instructions_returns_empty_even_with_docs_selected(self, mocker):
        from app.telephony.shared import get_eligible_kb_documents
        mocker.patch("app.kb.user_kb_repo.get_user_documents", return_value=[DOC_A])
        agent = {"selected_kb_doc_ids": ["doc-a"], "kb_instructions": ""}
        assert await get_eligible_kb_documents(agent, "user-1") == []

    async def test_eligible_when_selected_and_instructed(self, mocker):
        from app.telephony.shared import get_eligible_kb_documents
        mocker.patch("app.kb.user_kb_repo.get_user_documents", return_value=[DOC_A, DOC_B])
        agent = {"selected_kb_doc_ids": ["doc-a"], "kb_instructions": "Use the handbook."}
        result = await get_eligible_kb_documents(agent, "user-1")
        assert result == [DOC_A]

    async def test_only_docs_this_agent_actually_selected_are_considered(self, mocker):
        """A document the account owns but this agent never selected must
        never become eligible, even if kb_instructions happens to name it -
        proven here by selecting a third, unowned-by-this-agent id (doc-c)
        alongside doc-a so the multi-document "must be named" rule is the
        one deciding eligibility, not the single-document shortcut."""
        from app.telephony.shared import get_eligible_kb_documents
        doc_c = {"id": "doc-c", "display_name": "Unrelated.pdf"}
        mocker.patch("app.kb.user_kb_repo.get_user_documents", return_value=[DOC_A, DOC_B, doc_c])
        agent = {"selected_kb_doc_ids": ["doc-a", "doc-c"], "kb_instructions": "Use the Referral Policy."}
        result = await get_eligible_kb_documents(agent, "user-1")
        # doc-b is named but was never selected by this agent, so it's
        # excluded before the naming check even runs; doc-a/doc-c were
        # selected but neither is named, so the naming check excludes both.
        assert result == []


class TestResolveKbSearchDocIds:
    def test_matches_named_document_case_insensitively(self):
        from app.telephony.shared import resolve_kb_search_doc_ids
        result = resolve_kb_search_doc_ids({"document": "employee handbook.pdf"}, [DOC_A, DOC_B])
        assert result == ["doc-a"]

    def test_falls_back_to_all_eligible_when_no_match(self):
        from app.telephony.shared import resolve_kb_search_doc_ids
        result = resolve_kb_search_doc_ids({"document": "nonexistent.pdf"}, [DOC_A, DOC_B])
        assert set(result) == {"doc-a", "doc-b"}

    def test_falls_back_to_all_eligible_when_argument_omitted(self):
        from app.telephony.shared import resolve_kb_search_doc_ids
        result = resolve_kb_search_doc_ids({}, [DOC_A, DOC_B])
        assert set(result) == {"doc-a", "doc-b"}
