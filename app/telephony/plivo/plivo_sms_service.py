"""Outbound SMS via Plivo's REST API - same credential model and httpx
pattern as _place_plivo_call in app/hr_campaigns/hr_campaign_service.py,
just the Message resource instead of Call."""
import httpx


async def send_plivo_sms(
    auth_id: str, auth_token: str, src: str, dst: str, text: str,
) -> tuple[str | None, str | None]:
    """Returns (message_uuid, None) on success or (None, reason) on failure.
    Never raises - a failed text must not take the live call down with it."""
    src_digits = "".join(ch for ch in src if ch.isdigit())
    dst_digits = "".join(ch for ch in dst if ch.isdigit())
    if not (auth_id and auth_token):
        return None, "Plivo credentials missing"
    if not (src_digits and dst_digits):
        return None, "missing source or destination number"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.plivo.com/v1/Account/{auth_id}/Message/",
                auth=(auth_id, auth_token),
                json={"src": src_digits, "dst": dst_digits, "text": text},
            )
        if resp.status_code in (200, 201, 202):
            uuids = resp.json().get("message_uuid") or []
            return (uuids[0] if uuids else ""), None
        reason = f"Plivo returned {resp.status_code}: {resp.text[:200]}"
        print(f"[Plivo SMS] send to {dst_digits} failed - {reason}")
        return None, reason
    except Exception as e:
        print(f"[Plivo SMS] send to {dst_digits} raised: {e!r}")
        return None, str(e)
