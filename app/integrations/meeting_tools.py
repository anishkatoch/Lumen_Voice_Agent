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
KIND_CANCEL_MEETING = "internal_cancel_meeting"
KIND_RESCHEDULE_MEETING = "internal_reschedule_meeting"
MEETING_KINDS = (
    KIND_CHECK_AVAILABILITY, KIND_BOOK_MEETING,
    KIND_CANCEL_MEETING, KIND_RESCHEDULE_MEETING,
)

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
            {"name": "after", "type": "string",
             "description": "Earliest time of day to consider, format HH:MM 24-hour. Use 12:00 when the "
                            "caller asks for the afternoon, 15:00 for late afternoon. Omit for any time."},
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


def build_cancel_meeting_tool() -> dict:
    return {
        "name": "cancel_meeting",
        "kind": KIND_CANCEL_MEETING,
        "description": (
            "Cancel an upcoming meeting that was booked on this calendar. Ask for the email "
            "address the meeting was booked under (spell it back to confirm), and always "
            "confirm with the caller before cancelling."
        ),
        "parameters": [
            {"name": "attendee_email", "type": "string",
             "description": "The email address the meeting was booked under."},
            {"name": "start_time", "type": "string",
             "description": "The meeting's start time, format YYYY-MM-DDTHH:MM, if the caller has "
                            "more than one upcoming meeting. Leave empty otherwise."},
        ],
    }


def build_reschedule_meeting_tool() -> dict:
    return {
        "name": "reschedule_meeting",
        "kind": KIND_RESCHEDULE_MEETING,
        "description": (
            "Move an upcoming meeting to a new time. Ask for the email address the meeting was "
            "booked under, call check_availability for the new day first, and confirm the new "
            "time with the caller before rescheduling."
        ),
        "parameters": [
            {"name": "attendee_email", "type": "string",
             "description": "The email address the meeting was booked under."},
            {"name": "new_start_time", "type": "string",
             "description": "The new start time in the calendar's timezone, format YYYY-MM-DDTHH:MM."},
            {"name": "old_start_time", "type": "string",
             "description": "The current start time, format YYYY-MM-DDTHH:MM, if the caller has "
                            "more than one upcoming meeting. Leave empty otherwise."},
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
    return [
        build_check_availability_tool(), build_book_meeting_tool(),
        build_cancel_meeting_tool(), build_reschedule_meeting_tool(),
    ]


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
        "\nTo cancel or move an existing meeting you need the email address it was booked under. "
        "If the caller already gave their email earlier in this conversation (for example when "
        "booking), reuse it without asking again; only ask (and spell it back) when you do not "
        "have it yet. Confirm the caller really wants to cancel before calling cancel_meeting. "
        "For a new time, call check_availability first, and once the caller agrees to the new "
        "slot call reschedule_meeting immediately - do not re-confirm details you already have."
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
        if kind == KIND_CANCEL_MEETING:
            return await _cancel_meeting(args, agent, user_id)
        if kind == KIND_RESCHEDULE_MEETING:
            return await _reschedule_meeting(args, agent, user_id)
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
    # "afternoon"-style requests: without this, the 5-slot cap means a free
    # morning fills the whole answer and afternoon slots never surface, so
    # the agent wrongly tells the caller the afternoon is unavailable
    # (found in live testing 2026-11-12). The `after` arg raises the start
    # of each day's scan; a bad value just falls back to the booking hours.
    after_raw = (args.get("after") or "").strip()
    if after_raw:
        after = _parse_hhmm(after_raw, day_start)
        if after > day_start:
            day_start = min(after, day_end)
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


def _parse_meeting_row_time(row: dict, tz) -> datetime | None:
    try:
        return datetime.fromisoformat(str(row["start_time"]).replace("Z", "+00:00")).astimezone(tz)
    except (KeyError, ValueError):
        return None


def _spoken_time(dt: datetime) -> str:
    return dt.strftime("%A, %B %d at %I:%M %p")


async def _find_target_meeting(args: dict, agent: dict, user_id: str, start_key: str) -> tuple[dict | None, str | None]:
    """Resolve which upcoming meeting the caller means from their email (and,
    when they have several, the start time in args[start_key]). Returns
    (row, None) on success or (None, spoken_error) telling the model what to
    ask for next. Scoped to this account's own meetings table, so a caller
    can only ever touch meetings booked under the email they give."""
    from app.integrations.meetings_repo import get_upcoming_meetings

    email = (args.get("attendee_email") or "").strip()
    if not email or not _EMAIL_RE.match(email):
        return None, ("Ask the caller for the email address the meeting was booked under, "
                      "spell it back to confirm, and try again.")
    rows = await asyncio.to_thread(get_upcoming_meetings, user_id)
    matches = [r for r in rows if (r.get("attendee_email") or "").strip().lower() == email.lower()]
    if not matches:
        return None, (f"No upcoming meeting is booked under {email}. Double check the spelling "
                      "of the email with the caller.")

    tz = _agent_tz(agent)
    requested = (args.get(start_key) or "").strip()
    if requested:
        try:
            want = datetime.strptime(requested, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
        except ValueError:
            want = None
        if want is not None:
            for r in matches:
                row_start = _parse_meeting_row_time(r, tz)
                if row_start and abs((row_start - want).total_seconds()) < 60:
                    return r, None
            times = "; ".join(_spoken_time(t) for t in
                              filter(None, (_parse_meeting_row_time(r, tz) for r in matches)))
            return None, (f"No meeting under {email} starts at that time. Their upcoming "
                          f"meetings: {times}. Ask which one they mean.")
    if len(matches) > 1:
        times = "; ".join(_spoken_time(t) for t in
                          filter(None, (_parse_meeting_row_time(r, tz) for r in matches)))
        return None, (f"There are several upcoming meetings under {email}: {times}. "
                      "Ask the caller which one they mean, then call this tool again with "
                      "that start time.")
    return matches[0], None


async def _cancel_meeting(args: dict, agent: dict, user_id: str) -> str:
    from app.integrations.google_calendar_service import delete_calendar_event
    from app.integrations.meetings_repo import set_meeting_status

    row, err = await _find_target_meeting(args, agent, user_id, "start_time")
    if err:
        return err
    tz = _agent_tz(agent)
    when = _parse_meeting_row_time(row, tz)
    if row.get("google_event_id"):
        await delete_calendar_event(user_id, row["google_event_id"])
    print(f"[Meeting] cancelled meeting {row['id']} (event {row.get('google_event_id')}) "
          f"for user {user_id}")
    # Best-effort after the Google delete succeeded: a DB hiccup here must not
    # make the agent tell the caller the cancellation failed when the event
    # is already gone from the calendar (Google is the source of truth).
    try:
        await asyncio.to_thread(set_meeting_status, row["id"], "cancelled")
    except Exception as e:
        print(f"[Meeting] event cancelled on Google but failed to mark meetings row: {e}")
    return (
        f"Cancelled: the meeting on {_spoken_time(when) if when else 'the requested time'} "
        f"for {row.get('attendee_name') or 'the caller'} has been removed from the calendar"
        + (" and a cancellation email was sent." if row.get("attendee_email") else ".")
        + " Confirm this to the caller in one short sentence."
    )


async def _reschedule_meeting(args: dict, agent: dict, user_id: str) -> str:
    from app.integrations.google_calendar_service import fetch_busy_intervals, update_calendar_event_time
    from app.integrations.meetings_repo import update_meeting_time

    row, err = await _find_target_meeting(args, agent, user_id, "old_start_time")
    if err:
        return err
    if not row.get("google_event_id"):
        return ("This meeting has no linked calendar event, so it cannot be moved automatically. "
                "Offer to cancel it and book a fresh meeting at the new time instead.")

    tz = _agent_tz(agent)
    now = datetime.now(tz)
    raw_new = (args.get("new_start_time") or "").strip()
    try:
        new_start = datetime.strptime(raw_new, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
    except ValueError:
        return (f"Invalid new_start_time {raw_new!r} - it must look like 2026-11-13T14:30. "
                "Re-check the new date and time with the caller and try again.")
    if new_start <= now + timedelta(minutes=5):
        return "That new time is in the past or starts too soon. Ask the caller for a later time."

    duration = timedelta(minutes=int(agent.get("meeting_duration_minutes") or 30))
    buffer = timedelta(minutes=int(agent.get("meeting_buffer_minutes") or 0))
    new_end = new_start + duration

    # Conflict check for the new slot, ignoring the meeting's own current
    # block on the calendar - otherwise moving a meeting 15 minutes later
    # would falsely collide with itself.
    old_start = _parse_meeting_row_time(row, tz)
    try:
        old_end = datetime.fromisoformat(str(row["end_time"]).replace("Z", "+00:00")).astimezone(tz)
    except (KeyError, ValueError):
        old_end = old_start + duration if old_start else None
    busy = await fetch_busy_intervals(user_id, new_start - buffer, new_end + buffer)
    if old_start and old_end:
        busy = [(s, e) for s, e in busy if not (s >= old_start and e <= old_end)]
    if any(s < new_end + buffer and e > new_start - buffer for s, e in busy):
        return (f"The {_spoken_time(new_start)} slot is not free. Call check_availability and "
                "offer the caller open times instead. Never move the meeting to a time the "
                "caller has not agreed to.")

    tz_label = getattr(tz, "key", None) or "UTC"
    await update_calendar_event_time(user_id, row["google_event_id"], new_start, new_end, tz_label)
    print(f"[Meeting] rescheduled meeting {row['id']} (event {row['google_event_id']}) "
          f"to {new_start.isoformat()} for user {user_id}")
    # Best-effort after the Google move succeeded - same reasoning as cancel.
    try:
        await asyncio.to_thread(update_meeting_time, row["id"], new_start.isoformat(), new_end.isoformat())
    except Exception as e:
        print(f"[Meeting] event moved on Google but failed to update meetings row: {e}")
    return (
        f"Rescheduled: the meeting for {row.get('attendee_name') or 'the caller'} "
        f"moved from {_spoken_time(old_start) if old_start else 'its previous time'} "
        f"to {_spoken_time(new_start)}."
        + (" An updated invite was emailed." if row.get("attendee_email") else "")
        + " Confirm this to the caller in one short sentence."
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
            "call check_availability again, and offer new times. Never book a different time "
            "than the caller asked for until they have explicitly agreed to it."
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
    print(f"[Meeting] booked event {event.get('id')} at {start.isoformat()} "
          f"for user {user_id} (attendee: {attendee_email or 'none'})")
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
