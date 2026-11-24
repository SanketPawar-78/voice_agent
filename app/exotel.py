import httpx

from .config import settings

# call_sid -> agent name for calls we placed ourselves (marks them "outbound")
pending_outbound: dict[str, str] = {}


async def make_outbound_call(to_number: str, agent: str) -> dict:
    """Exotel calls `to_number`, then connects the answered call to the voice-stream flow."""
    s = settings
    url = (f"https://{s.exotel_api_key}:{s.exotel_api_token}@{s.exotel_subdomain}"
           f"/v1/Accounts/{s.exotel_sid}/Calls/connect.json")
    data = {
        "From": to_number,
        "CallerId": s.exotel_number,
        "Url": f"http://my.exotel.com/{s.exotel_sid}/exoml/start_voice/{s.exotel_flow_app_id}",
        "CustomField": agent,
        "StatusCallback": f"https://{s.public_host}/exotel/status",
    }
    async with httpx.AsyncClient() as c:
        r = await c.post(url, data=data, timeout=20)
        r.raise_for_status()
        body = r.json()
    sid = (body.get("Call") or {}).get("Sid")
    if sid:
        pending_outbound[sid] = agent   # picked up by CallSession.on_start via call_sid
    return body
