"""
Real end-to-end check (against your ACTUAL Supabase DB, not mocks) that the
Call Analytics migration (0017) is applied and the write path actually works.

Does exactly what app/hr_scheduler.py does on a real dial failure:
  1. record_failed_call(...)   - same function the scheduler calls
  2. reads it back via get_conversations(...) - same function /api/conversations uses
  3. asserts call_status == "failed" and failure_reason is the exact string written
  4. deletes the test row it created, so nothing fake lingers in your dashboard

If migration 0017 hasn't been applied, step 1 will print a
"[Call Analytics] ... retrying without them" fallback message and step 3 will
fail loudly here (never silently) - the test row would still exist, but marked
"completed" with no failure_reason, because the server fell back to legacy
columns, exactly as designed to keep real calls from breaking.

Usage:
    uv run python scripts/verify_call_analytics.py
    uv run python scripts/verify_call_analytics.py --user-id <your-user-id>
        (defaults to the first user found in the conversations table)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversation_repo import record_failed_call, get_conversations  # noqa: E402
from app.database import get_supabase  # noqa: E402

TEST_REASON = "VERIFY SCRIPT - this is a test row, safe to ignore/delete"
TEST_TITLE = "Call Analytics verification"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", default=None, help="user_id to write the test row under")
    args = parser.parse_args()

    db = get_supabase()

    user_id = args.user_id
    if not user_id:
        existing = db.table("conversations").select("user_id").limit(1).execute().data
        if not existing:
            print("No existing conversations found to borrow a user_id from - pass --user-id explicitly.")
            sys.exit(1)
        user_id = existing[0]["user_id"]
        print(f"No --user-id given, using an existing one from your data: {user_id}")

    print(f"\n1. Writing a test failed-call row via record_failed_call(user_id={user_id!r}, ...)")
    record_failed_call(user_id, TEST_TITLE, None, TEST_REASON)

    print("2. Reading it back via get_conversations(...) (same function /api/conversations uses)")
    rows = [c for c in get_conversations(user_id) if c.get("title") == TEST_TITLE]
    if not rows:
        print("\nFAIL: no row was created at all. Something is wrong beyond just the migration - "
              "check server-side DB credentials/permissions.")
        sys.exit(1)

    row = rows[0]
    print(f"   Found row: id={row['id']} call_status={row.get('call_status')!r} "
          f"failure_reason={row.get('failure_reason')!r}")

    ok = row.get("call_status") == "failed" and row.get("failure_reason") == TEST_REASON

    # Clean up regardless of outcome - never leave a fake row in the dashboard.
    db.table("conversations").delete().eq("id", row["id"]).execute()
    print(f"3. Cleaned up test row {row['id']}")

    if ok:
        print("\nPASS - migration 0017 is applied and Call Analytics failure tracking works end to end.")
    else:
        print("\nFAIL - the row was created but call_status/failure_reason weren't set as expected.")
        print("This means migration 0017 has NOT been applied (the code fell back to legacy columns).")
        print("Run: uv run alembic upgrade head")
        sys.exit(1)


if __name__ == "__main__":
    main()
