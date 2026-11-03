"""Constants shared by both the Twilio and Plivo telephony pipelines."""

# Phone calls can't tolerate a long silent wait on a webhook/tool call - while
# one is in flight, is_processing stays True and the caller's mic is only
# checked against the (intentionally strict) barge-in VAD threshold, not
# normal listening, so a slow/unreachable webhook makes the call look dead.
# Hard-cap regardless of the per-function timeout_ms an admin configured.


PHONE_WEBHOOK_MAX_TIMEOUT_S = 15

_CALL_STATUS_REASONS = {
    "busy": "The line was busy.",
    "no-answer": "The call was not answered.",
    "failed": "The call could not be connected.",
    "canceled": "The call was canceled before it connected.",
}
