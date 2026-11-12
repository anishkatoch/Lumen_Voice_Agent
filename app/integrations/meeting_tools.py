"""Meeting booking tools for voice calls - check_availability + book_meeting.

Synthetic tools in the same mold as build_kb_search_tool /
build_record_answer_tool (app/telephony/shared.py): never stored in
agent_functions, dispatched by a "kind" marker, added to a call's tool list
only when the agent has meeting_booking_enabled AND the agent's owner has a
live Google Calendar connection (get_meeting_tool_functions). Handlers are
shared by all four execution paths (Twilio cascade, Plivo cascade, browser
cascade, and both Realtime bridges) via execute_meeting_tool, so a fix lands
once.

Every handler returns a plain spoken-English string (like the KB search
tool), never raises: an exception mid-call would kill the conversation, so
failures fail open to a polite "calendar unavailable" line the model can
relay.
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone

KIND_CHECK_AVAILABILITY = "internal_check_availability"
KIND_BOOK_MEETING = "internal_book_meeting"
MEETING_KINDS = (KIND_CHECK_AVAILABILITY, KIND_BOOK_MEETING)

# How far ahead one check_availability call scans, and the minimum notice
# before a bookable slot (a caller can't book a meeting starting mid-call).
SEARCH_DAYS = 4
MIN_LEAD_MINUTES = 15
MAX_SLOTS_SPOKEN = 5

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

_CALENDAR_DOWN_MSG = (
    "The calendar system could not be reached just now. Apologize, offer to "
    "take their name and contact details, and say someone will follow up to "
    "schedule the meeting."
)
_RECONNECT_MSG = (
    "The calendar connection has expired on our side, so booking is not "
    "possible on this call. Apologize, take their contact details, and say "
    "someone will follow up to schedule the meeting."
)


def _agent_tz(agent: dict):
    """The agent's configured timezone, falling back to UTC when the name is
    unknown or tzdata is unavailable - a broken timezone must never break a
    call, it just makes the spoken times UTC."""
    name = (agent.get("meeting_timezone") or "UTC").strip() or "UTC"
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = str(value).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    return default


def compute_free_slots(
    busy: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
    buffer_minutes: int = 0,
    day_start: tuple[int, int] = (9, 0),
    day_end: tuple[int, int] = (17, 0),
    step_minutes: int = 30,
    earliest: datetime | None = None,
    max_slots: int = MAX_SLOTS_SPOKEN,
) -> list[datetime]:
    """Pure slot computation so it can be unit tested without Google.

    Walks each day in [window_start, window_end) within the configured
    booking hours (interpreted in window_start's timezone), proposing slots
    on step_minutes boundaries. A candidate [t, t + duration], padded by
    buffer_minutes on both sides, must not overlap any busy interval and
    must start at or after `earliest`.
    """
    duration = timedelta(minutes=max(int(duration_minutes), 5))
    buffer = timedelta(minutes=max(int(buffer_minutes), 0))
    step = timedelta(minutes=max(int(step_minutes), 5))
    tz = window_start.tzinfo
    slots: list[datetime] = []

    day = window_start.date()
    while datetime.combine(day, datetime.min.time(), tzinfo=tz) < window_end:
        t = datetime(day.year, day.month, day.day, day_start[0], day_start[1], tzinfo=tz)
        day_close = datetime(day.year, day.month, day.day, day_end[0], day_end[1], tzinfo=tz)
        while t + duration <= day_close:
            if t >= window_start and (earliest is None or t >= earliest) and t + duration <= window_end:
                padded_start = t - buffer
                padded_end = t + duration + buffer
                if not any(b_start < padded_end and b_end > padded_start for b_start, b_end in busy):
                    slots.append(t)
                    if len(slots) >= max_slots:
                        return slots
            t += step
        day += timedelta(days=1)
    return slots


def build_check_availability_tool() -> dict:
    return {
        "name": "check_availability",
        "kind": KIND_CHECK_AVAILABILITY,
        "description": (
            "Check the calendar for open meeting slots on or after a date. "
            "Always call this before offering or confirming any meeting time - "
            "never invent availability from memory."
        ),
        "parameters": [
            {"name": "date", "type": "string",
             "description": "The day the caller asked about, format YYYY-MM-DD. Omit to check starting today."},
        ],
    }


def build_book_meeting_tool() -> dict:
    return {
        "name": "book_meeting",
        "kind": KIND_BOOK_MEETING,
        "description": (
            "Book a meeting on the calendar at an exact start time. Only call "
            "this after check_availability showed the slot open and the caller "
            "confirmed the time, their name, and (if they want an emailed "
            "invite) their email address."
        ),
        "parameters": [
            {"name": "start_time", "type": "string",
             "description": "Meeting start in the calendar's timezone, format YYYY-MM-DDTHH:MM, 24-hour clock."},
            {"name": "attendee_name", "type": "string",
             "description": "The caller's name."},
            {"name": "attendee_email", "type": "string",
             "description": "The caller's email for the calendar invite. Spell it back and get confirmation before booking. Omit if they decline."},
            {"name": "topic", "type": "string",
             "description": "Short subject for the meeting."},
        ],
    }


async def get_meeting_tool_functions(agent: dict, user_id: str) -> list[dict]:
    """The gate, mirroring get_eligible_kb_documents' philosophy: the tools
    exist on a call only when the agent setting is on AND the owner's Google
    Calendar is actually connected. An enabled agent whose owner never
    connected (or whose tokens no longer decrypt) behaves as if booking were
    off, instead of offering tools that can only fail mid-call."""
    if not agent.get("meeting_booking_enabled"):
        return []
    try:
        from app.integrations.integrations_repo import GOOGLE_CALENDAR, get_integration
        integration = await asyncio.to_thread(get_integration, user_id, GOOGLE_CALENDAR)
    except Exception as e:
        print(f"[Meeting] integration lookup failed, booking disabled for this call: {e}")
        return []
    if not integration:
        print(f"[Meeting] agent {agent.get('id')} has booking enabled but user {user_id} "
              f"has no Google Calendar connected - tools not offered")
        return []
    return [build_check_availability_tool(), build_book_meeting_tool()]


def build_meeting_system_context(agent: dict) -> str:
    """Prompt block appended to the system context when the meeting tools are
    on a call. Spells out 'now' in the agent's timezone - without it the
    model cannot resolve 'tomorrow at 2' into a concrete start_time."""
    tz = _agent_tz(agent)
    now = datetime.now(tz)
    duration = int(agent.get("meeting_duration_minutes") or 30)
    hours_start = (agent.get("booking_hours_start") or "09:00").strip()
    hours_end = (agent.get("booking_hours_end") or "17:00").strip()
    extra = (agent.get("meeting_instructions") or "").strip()
    tz_label = getattr(tz, "key", None) or "UTC"
    return (
        "\n\n--- MEETING BOOKING ---"
        f"\nYou can book meetings on the calendar. Right now it is {now.strftime('%A, %B %d, %Y at %I:%M %p')} "
        f"({tz_label}). All meeting times you say or book are in this timezone."
        f"\nMeetings are {duration} minutes long, bookable between {hours_start} and {hours_end}."
        "\nFlow: ask what day works for them, call check_availability for that day, offer a few "
        "of the returned open times, then confirm the chosen time, the caller's name, and their "
        "email address (spell the email back to them) before calling book_meeting. If they do "
        "not want an email invite, book without one."
        "\nNever promise a time check_availability did not return, and never claim a meeting is "
        "booked unless book_meeting confirmed it."
        + (f"\nAdditional booking rules from the business:\n{extra}" if extra else "")
    )


async def execute_meeting_tool(
    fn: dict, args: dict, agent: dict, user_id: str, conversation_id: str | None = None,
) -> str:
    """Single dispatch point for both meeting tools, shared by every pipeline.
    Returns spoken-English text for the model; never raises."""
    from app.integrations.google_calendar_service import CalendarAuthError
    try:
        kind = fn.get("kind")
        if kind == KIND_CHECK_AVAILABILITY:
            return await _check_availability(args, agent, user_id)
        if kind == KIND_BOOK_MEETING:
            return await _book_meeting(args, agent, user_id, conversation_id)
        return "This tool is not available on this call."
    except CalendarAuthError as e:
        print(f"[Meeting] auth error for user {user_id}: {e}")
        return _RECONNECT_MSG
    except Exception as e:
        print(f"[Meeting] tool {fn.get('name')} failed: {e!r}")
        return _CALENDAR_DOWN_MSG


def _booking_window(agent: dict, from_date) -> tuple:
    """(window_start, window_end, day_start, day_end, tz) for a scan starting
    at from_date (a date in the agent's timezone)."""
    tz = _agent_tz(agent)
    day_start = _parse_hhmm(agent.get("booking_hours_start") or "09:00", (9, 0))
    day_end = _parse_hhmm(agent.get("booking_hours_end") or "17:00", (17, 0))
    window_start = datetime(from_date.year, from_date.month, from_date.day, tzinfo=tz)
    window_end = window_start + timedelta(days=SEARCH_DAYS)
    return window_start, window_end, day_start, day_end, tz


async def _check_availability(args: dict, agent: dict, user_id: str) -> str:
    from app.integrations.google_calendar_service import fetch_busy_intervals

    tz = _agent_tz(agent)
    now = datetime.now(tz)
    requested = (args.get("date") or "").strip()
    try:
        from_date = datetime.strptime(requested, "%Y-%m-%d").date() if requested else now.date()
    except ValueError:
        from_date = now.date()
    if from_date < now.date():
        from_date = now.date()

    window_start, window_end, day_start, day_end, tz = _booking_window(agent, from_date)
    busy = await fetch_busy_intervals(user_id, window_start, window_end)
    slots = compute_free_slots(
        busy=[(s.astimezone(tz), e.astimezone(tz)) for s, e in busy],
        window_start=window_start,
        window_end=window_end,
        duration_minutes=int(agent.get("meeting_duration_minutes") or 30),
        buffer_minutes=int(agent.get("meeting_buffer_minutes") or 0),
        day_start=day_start,
        day_end=day_end,
        earliest=now + timedelta(minutes=MIN_LEAD_MINUTES),
    )
    if not slots:
        return (
            f"No open slots between {window_start.strftime('%A, %B %d')} and "
            f"{(window_end - timedelta(days=1)).strftime('%A, %B %d')}. Ask the caller "
            "for a later date and check again."
        )
    spoken = "; ".join(s.strftime("%A, %B %d at %I:%M %p") for s in slots)
    return (
        f"Open slots (each {int(agent.get('meeting_duration_minutes') or 30)} minutes): {spoken}. "
        "Offer the caller two or three of these, not the whole list."
    )


async def _book_meeting(args: dict, agent: dict, user_id: str, conversation_id: str | None) -> str:
    from app.integrations.google_calendar_service import create_calendar_event, fetch_busy_intervals

    tz = _agent_tz(agent)
    now = datetime.now(tz)

    raw_start = (args.get("start_time") or "").strip()
    try:
        start = datetime.strptime(raw_start, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
    except ValueError:
        return (
            f"Invalid start_time {raw_start!r} - it must look like 2026-11-13T14:30. "
            "Re-check the date and time with the caller and try again."
        )
    if start <= now + timedelta(minutes=5):
        return "That time is in the past or starts too soon. Ask the caller for a later time."

    attendee_email = (args.get("attendee_email") or "").strip() or None
    if attendee_email and not _EMAIL_RE.match(attendee_email):
        return (
            f"The email address {attendee_email!r} does not look valid. Spell it back to the "
            "caller letter by letter, correct it, and call book_meeting again - or book "
            "without an email if they prefer."
        )
    attendee_name = (args.get("attendee_name") or "").strip() or "Guest"
    topic = (args.get("topic") or "").strip() or "Meeting"

    duration = timedelta(minutes=int(agent.get("meeting_duration_minutes") or 30))
    buffer = timedelta(minutes=int(agent.get("meeting_buffer_minutes") or 0))
    end = start + duration

    # Re-check the slot right before writing - the caller may have spent
    # minutes talking since check_availability, and another call (or the
    # owner) can have taken the slot in the meantime.
    busy = await fetch_busy_intervals(user_id, start - buffer, end + buffer)
    if any(b_start < end + buffer and b_end > start - buffer for b_start, b_end in busy):
        return (
            f"The {start.strftime('%A, %B %d at %I:%M %p')} slot was just taken. Apologize, "
            "call check_availability again, and offer new times."
        )

    summary = f"{topic} - {attendee_name}"
    description = (
        f"Booked by voice agent {agent.get('name') or ''} for {attendee_name}"
        + (f" ({attendee_email})" if attendee_email else "")
    )
    tz_label = getattr(tz, "key", None) or "UTC"
    event = await create_calendar_event(
        user_id, start, end, summary, description, attendee_email, tz_label,
    )

    meet_link = event.get("hangoutLink") or ""
    # Audit row is best-effort: the calendar event already exists, and a DB
    # hiccup here must not make the agent tell the caller the booking failed.
    try:
        from app.integrations.meetings_repo import create_meeting
        await asyncio.to_thread(
            create_meeting, user_id, agent.get("id") or "", conversation_id,
            event.get("id"), attendee_name, attendee_email, topic,
            start.isoformat(), end.isoformat(), meet_link or None,
        )
    except Exception as e:
        print(f"[Meeting] booked on Google but failed to save meetings row: {e}")

    return (
        f"Booked: {topic} on {start.strftime('%A, %B %d at %I:%M %p')} "
        f"({int(duration.total_seconds() // 60)} minutes) for {attendee_name}."
        + (f" A calendar invite with the meeting link was emailed to {attendee_email}."
           if attendee_email else " No email invite was sent.")
        + " Confirm this to the caller in one short sentence."
    )
