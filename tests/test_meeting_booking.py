"""
Tests for the Google Calendar meeting booking feature:
- compute_free_slots (pure slot math: hours, duration, busy overlap, buffer)
- tool gating (get_meeting_tool_functions - setting AND connection required)
- the check_availability / book_meeting handlers (Google mocked all the way
  down, per tests/conftest.py's rule that an unmocked network call fails DNS)
- token encryption round-trip in integrations_repo
- update_agent accepting the new per-agent booking settings
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.asyncio

UTC = timezone.utc

# A fixed future Monday so "now" (test run time) never filters these slots
# out via the MIN_LEAD_MINUTES / earliest logic.
DAY = datetime(2030, 1, 7, tzinfo=UTC)  # Monday

AGENT = {
    "id": "agent-1",
    "name": "Meeting Assistant",
    "meeting_booking_enabled": True,
    "meeting_duration_minutes": 30,
    "meeting_buffer_minutes": 0,
    "meeting_timezone": "UTC",
    "booking_hours_start": "09:00",
    "booking_hours_end": "17:00",
}

USER_ID = "11111111-1111-1111-1111-111111111111"


def _slot(h, m=0):
    return DAY.replace(hour=h, minute=m)


# ── compute_free_slots ───────────────────────────────────────────────────────

class TestComputeFreeSlots:
    def test_empty_calendar_offers_slots_from_day_start(self):
        from app.integrations.meeting_tools import compute_free_slots
        slots = compute_free_slots([], DAY, DAY + timedelta(days=1), 30)
        assert slots[0] == _slot(9)
        assert slots[1] == _slot(9, 30)
        assert len(slots) == 5  # capped at max_slots

    def test_busy_interval_blocks_overlapping_slots(self):
        from app.integrations.meeting_tools import compute_free_slots
        busy = [(_slot(9), _slot(12))]
        slots = compute_free_slots(busy, DAY, DAY + timedelta(days=1), 30)
        assert slots[0] == _slot(12)

    def test_partial_overlap_blocks_the_slot(self):
        """A meeting 09:15-09:45 must block both the 09:00 and 09:30 slots."""
        from app.integrations.meeting_tools import compute_free_slots
        busy = [(_slot(9, 15), _slot(9, 45))]
        slots = compute_free_slots(busy, DAY, DAY + timedelta(days=1), 30)
        assert _slot(9) not in slots
        assert _slot(9, 30) not in slots
        assert slots[0] == _slot(10)

    def test_buffer_pads_around_busy_intervals(self):
        from app.integrations.meeting_tools import compute_free_slots
        busy = [(_slot(10), _slot(10, 30))]
        slots = compute_free_slots(busy, DAY, DAY + timedelta(days=1), 30, buffer_minutes=15)
        # 09:30 slot ends 10:00; padded by 15min it reaches 10:15, into the busy block
        assert _slot(9, 30) not in slots
        assert _slot(9) in slots

    def test_duration_must_fit_before_day_end(self):
        from app.integrations.meeting_tools import compute_free_slots
        slots = compute_free_slots([], DAY, DAY + timedelta(days=1), 60, max_slots=100)
        assert slots[-1] == _slot(16)  # 16:00 + 60min == 17:00 close

    def test_earliest_filters_past_slots(self):
        from app.integrations.meeting_tools import compute_free_slots
        slots = compute_free_slots([], DAY, DAY + timedelta(days=1), 30, earliest=_slot(14, 10))
        assert slots[0] == _slot(14, 30)

    def test_fully_booked_day_spills_into_next_day(self):
        from app.integrations.meeting_tools import compute_free_slots
        busy = [(_slot(9), _slot(17))]
        slots = compute_free_slots(busy, DAY, DAY + timedelta(days=2), 30)
        assert slots[0] == (DAY + timedelta(days=1)).replace(hour=9)

    def test_no_slots_when_everything_busy(self):
        from app.integrations.meeting_tools import compute_free_slots
        busy = [(DAY, DAY + timedelta(days=4))]
        slots = compute_free_slots(busy, DAY, DAY + timedelta(days=4), 30)
        assert slots == []


# ── tool gating ──────────────────────────────────────────────────────────────

class TestGetMeetingToolFunctions:
    async def test_disabled_agent_gets_no_tools(self, mocker):
        from app.integrations.meeting_tools import get_meeting_tool_functions
        lookup = mocker.patch("app.integrations.integrations_repo.get_integration")
        agent = {**AGENT, "meeting_booking_enabled": False}
        assert await get_meeting_tool_functions(agent, USER_ID) == []
        lookup.assert_not_called()

    async def test_enabled_but_not_connected_gets_no_tools(self, mocker):
        from app.integrations.meeting_tools import get_meeting_tool_functions
        mocker.patch("app.integrations.integrations_repo.get_integration", return_value=None)
        assert await get_meeting_tool_functions(AGENT, USER_ID) == []

    async def test_enabled_and_connected_gets_both_tools(self, mocker):
        from app.integrations.meeting_tools import get_meeting_tool_functions
        mocker.patch(
            "app.integrations.integrations_repo.get_integration",
            return_value={"refresh_token": "rt", "calendar_id": "primary"},
        )
        tools = await get_meeting_tool_functions(AGENT, USER_ID)
        assert [t["name"] for t in tools] == ["check_availability", "book_meeting"]
        assert tools[0]["kind"] == "internal_check_availability"
        assert tools[1]["kind"] == "internal_book_meeting"

    async def test_integration_lookup_failure_fails_open_to_no_tools(self, mocker):
        """A DB hiccup during call setup must disable booking for the call,
        never abort the call itself - same posture as admin_config DEFAULTS."""
        from app.integrations.meeting_tools import get_meeting_tool_functions
        mocker.patch(
            "app.integrations.integrations_repo.get_integration",
            side_effect=RuntimeError("db down"),
        )
        assert await get_meeting_tool_functions(AGENT, USER_ID) == []


# ── check_availability handler ───────────────────────────────────────────────

class TestCheckAvailability:
    async def test_returns_spoken_slots_skipping_busy(self, mocker):
        from app.integrations.meeting_tools import build_check_availability_tool, execute_meeting_tool
        mocker.patch(
            "app.integrations.google_calendar_service.fetch_busy_intervals",
            new=AsyncMock(return_value=[(_slot(9), _slot(12))]),
        )
        result = await execute_meeting_tool(
            build_check_availability_tool(), {"date": "2030-01-07"}, AGENT, USER_ID,
        )
        assert "12:00 PM" in result
        assert "09:00 AM" not in result
        assert "Monday, January 07" in result

    async def test_no_slots_tells_model_to_ask_for_another_date(self, mocker):
        from app.integrations.meeting_tools import build_check_availability_tool, execute_meeting_tool
        mocker.patch(
            "app.integrations.google_calendar_service.fetch_busy_intervals",
            new=AsyncMock(return_value=[(DAY, DAY + timedelta(days=10))]),
        )
        result = await execute_meeting_tool(
            build_check_availability_tool(), {"date": "2030-01-07"}, AGENT, USER_ID,
        )
        assert "No open slots" in result

    async def test_garbage_date_falls_back_to_today_not_crash(self, mocker):
        from app.integrations.meeting_tools import build_check_availability_tool, execute_meeting_tool
        fetch = AsyncMock(return_value=[])
        mocker.patch("app.integrations.google_calendar_service.fetch_busy_intervals", new=fetch)
        result = await execute_meeting_tool(
            build_check_availability_tool(), {"date": "next tuesday"}, AGENT, USER_ID,
        )
        assert isinstance(result, str) and result
        # The scan window starts today, not at some parse-error default
        window_start = fetch.call_args[0][1]
        assert window_start.date() == datetime.now(UTC).date()

    async def test_calendar_error_returns_polite_fallback_not_exception(self, mocker):
        from app.integrations.meeting_tools import build_check_availability_tool, execute_meeting_tool
        mocker.patch(
            "app.integrations.google_calendar_service.fetch_busy_intervals",
            new=AsyncMock(side_effect=RuntimeError("Google freeBusy failed (500)")),
        )
        result = await execute_meeting_tool(
            build_check_availability_tool(), {"date": "2030-01-07"}, AGENT, USER_ID,
        )
        assert "could not be reached" in result

    async def test_revoked_grant_tells_model_connection_expired(self, mocker):
        from app.integrations.google_calendar_service import CalendarAuthError
        from app.integrations.meeting_tools import build_check_availability_tool, execute_meeting_tool
        mocker.patch(
            "app.integrations.google_calendar_service.fetch_busy_intervals",
            new=AsyncMock(side_effect=CalendarAuthError("reconnect")),
        )
        result = await execute_meeting_tool(
            build_check_availability_tool(), {"date": "2030-01-07"}, AGENT, USER_ID,
        )
        assert "expired" in result


# ── book_meeting handler ─────────────────────────────────────────────────────

class TestBookMeeting:
    def _mocks(self, mocker, busy=None, event=None):
        fetch = AsyncMock(return_value=busy or [])
        create = AsyncMock(return_value=event or {"id": "evt-1", "hangoutLink": "https://meet.google.com/abc"})
        mocker.patch("app.integrations.google_calendar_service.fetch_busy_intervals", new=fetch)
        mocker.patch("app.integrations.google_calendar_service.create_calendar_event", new=create)
        save = mocker.patch("app.integrations.meetings_repo.create_meeting", return_value={"id": "m-1"})
        return fetch, create, save

    async def test_books_free_slot_and_saves_record(self, mocker):
        from app.integrations.meeting_tools import build_book_meeting_tool, execute_meeting_tool
        _, create, save = self._mocks(mocker)
        result = await execute_meeting_tool(
            build_book_meeting_tool(),
            {"start_time": "2030-01-07T10:00", "attendee_name": "Alice",
             "attendee_email": "alice@example.com", "topic": "Product demo"},
            AGENT, USER_ID, conversation_id="conv-1",
        )
        assert "Booked" in result
        assert "alice@example.com" in result
        start_arg, end_arg = create.call_args[0][1], create.call_args[0][2]
        assert start_arg == _slot(10)
        assert end_arg == _slot(10, 30)
        assert create.call_args[0][5] == "alice@example.com"
        save.assert_called_once()

    async def test_taken_slot_is_rejected_and_nothing_created(self, mocker):
        """Race protection: the slot was free at check_availability time but
        got taken while the caller talked - book must re-check and refuse."""
        from app.integrations.meeting_tools import build_book_meeting_tool, execute_meeting_tool
        _, create, save = self._mocks(mocker, busy=[(_slot(10), _slot(10, 30))])
        result = await execute_meeting_tool(
            build_book_meeting_tool(),
            {"start_time": "2030-01-07T10:00", "attendee_name": "Alice"},
            AGENT, USER_ID,
        )
        assert "just taken" in result
        create.assert_not_called()
        save.assert_not_called()

    async def test_invalid_email_asks_to_respell_without_booking(self, mocker):
        from app.integrations.meeting_tools import build_book_meeting_tool, execute_meeting_tool
        _, create, _ = self._mocks(mocker)
        result = await execute_meeting_tool(
            build_book_meeting_tool(),
            {"start_time": "2030-01-07T10:00", "attendee_name": "Alice",
             "attendee_email": "alice at example dot com"},
            AGENT, USER_ID,
        )
        assert "does not look valid" in result
        create.assert_not_called()

    async def test_booking_without_email_skips_invite(self, mocker):
        from app.integrations.meeting_tools import build_book_meeting_tool, execute_meeting_tool
        _, create, _ = self._mocks(mocker)
        result = await execute_meeting_tool(
            build_book_meeting_tool(),
            {"start_time": "2030-01-07T10:00", "attendee_name": "Bob", "topic": "Intro call"},
            AGENT, USER_ID,
        )
        assert "Booked" in result
        assert "No email invite" in result
        assert create.call_args[0][5] is None

    async def test_past_time_is_rejected(self, mocker):
        from app.integrations.meeting_tools import build_book_meeting_tool, execute_meeting_tool
        _, create, _ = self._mocks(mocker)
        result = await execute_meeting_tool(
            build_book_meeting_tool(),
            {"start_time": "2020-01-07T10:00", "attendee_name": "Alice"},
            AGENT, USER_ID,
        )
        assert "past" in result
        create.assert_not_called()

    async def test_malformed_start_time_is_rejected_with_format_hint(self, mocker):
        from app.integrations.meeting_tools import build_book_meeting_tool, execute_meeting_tool
        _, create, _ = self._mocks(mocker)
        result = await execute_meeting_tool(
            build_book_meeting_tool(),
            {"start_time": "tomorrow at 2pm", "attendee_name": "Alice"},
            AGENT, USER_ID,
        )
        assert "Invalid start_time" in result
        create.assert_not_called()

    async def test_meetings_row_failure_does_not_unbook_the_meeting(self, mocker):
        """The Google event already exists when the audit insert runs - a DB
        error there must not make the agent tell the caller booking failed."""
        from app.integrations.meeting_tools import build_book_meeting_tool, execute_meeting_tool
        self._mocks(mocker)
        mocker.patch(
            "app.integrations.meetings_repo.create_meeting",
            side_effect=RuntimeError("db down"),
        )
        result = await execute_meeting_tool(
            build_book_meeting_tool(),
            {"start_time": "2030-01-07T10:00", "attendee_name": "Alice"},
            AGENT, USER_ID,
        )
        assert "Booked" in result


# ── prompt context ───────────────────────────────────────────────────────────

class TestMeetingSystemContext:
    def test_contains_schedule_settings_and_flow(self):
        from app.integrations.meeting_tools import build_meeting_system_context
        ctx = build_meeting_system_context(AGENT)
        assert "--- MEETING BOOKING ---" in ctx
        assert "30 minutes" in ctx
        assert "09:00 and 17:00" in ctx
        assert "check_availability" in ctx and "book_meeting" in ctx

    def test_owner_instructions_are_included(self):
        from app.integrations.meeting_tools import build_meeting_system_context
        ctx = build_meeting_system_context({**AGENT, "meeting_instructions": "Only book demo calls."})
        assert "Only book demo calls." in ctx

    def test_unknown_timezone_falls_back_instead_of_crashing(self):
        from app.integrations.meeting_tools import build_meeting_system_context
        ctx = build_meeting_system_context({**AGENT, "meeting_timezone": "Mars/Olympus_Mons"})
        assert "--- MEETING BOOKING ---" in ctx


# ── token encryption ─────────────────────────────────────────────────────────

class TestTokenEncryption:
    def test_round_trip(self):
        from app.integrations.integrations_repo import decrypt_token, encrypt_token
        ct = encrypt_token("ya29.secret-token")
        assert ct != "ya29.secret-token"
        assert decrypt_token(ct) == "ya29.secret-token"

    def test_garbage_ciphertext_returns_none_not_exception(self):
        from app.integrations.integrations_repo import decrypt_token
        assert decrypt_token("not-a-fernet-token") is None
        assert decrypt_token("") is None

    def test_get_integration_treats_undecryptable_refresh_as_disconnected(self, mocker):
        from app.integrations import integrations_repo
        db = mocker.MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .limit.return_value.execute.return_value.data = [
                {"refresh_token": "plaintext-or-old-key-ciphertext", "access_token": ""}
            ]
        mocker.patch("app.integrations.integrations_repo.get_supabase", return_value=db)
        assert integrations_repo.get_integration(USER_ID, "google_calendar") is None

    def test_reconnect_without_new_refresh_token_keeps_old_one(self, mocker):
        """Google only returns a refresh_token on first consent unless
        prompt=consent is forced - a re-connect response without one must not
        wipe the stored token."""
        from app.integrations import integrations_repo
        db = mocker.MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .limit.return_value.execute.return_value.data = [
                {"id": "row-1", "refresh_token": "old-encrypted-value"}
            ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": "row-1"}]
        mocker.patch("app.integrations.integrations_repo.get_supabase", return_value=db)
        integrations_repo.upsert_integration(
            USER_ID, "google_calendar", "new-access", None, None, "a@b.co",
        )
        written = db.table.return_value.update.call_args[0][0]
        assert written["refresh_token"] == "old-encrypted-value"


# ── agent settings persistence ───────────────────────────────────────────────

class TestAgentMeetingSettings:
    def test_update_agent_accepts_meeting_fields_and_drops_unknown(self, mocker):
        from app.agents import agent_repo
        db = mocker.MagicMock()
        db.table.return_value.update.return_value.eq.return_value.eq.return_value \
            .execute.return_value.data = [{"id": "agent-1"}]
        mocker.patch("app.agents.agent_repo.get_supabase", return_value=db)
        agent_repo.update_agent(
            "agent-1", USER_ID,
            meeting_booking_enabled=True,
            meeting_duration_minutes=45,
            meeting_timezone="Asia/Kolkata",
            booking_hours_start="10:00",
            bogus_field="nope",
        )
        written = db.table.return_value.update.call_args[0][0]
        assert written["meeting_booking_enabled"] is True
        assert written["meeting_duration_minutes"] == 45
        assert written["meeting_timezone"] == "Asia/Kolkata"
        assert written["booking_hours_start"] == "10:00"
        assert "bogus_field" not in written
