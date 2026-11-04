"""Shared auth dependencies: JWT verification and the get_current_user dependency."""
import json
from typing import Optional

import httpx
import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import SUPABASE_URL, SUPABASE_JWT_SECRET


security = HTTPBearer()

_jwks_cache: Optional[dict] = None

async def _get_jwks(force_refresh: bool = False) -> dict:
    global _jwks_cache
    if _jwks_cache and not force_refresh:
        return _jwks_cache
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")
        resp.raise_for_status()
        _jwks_cache = resp.json()
    return _jwks_cache

def _find_jwk(jwks: dict, kid: str) -> dict | None:
    return next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)

async def decode_supabase_token(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")
    kid = header.get("kid")

    if alg != "HS256" and kid:
        try:
            jwks = await _get_jwks()
            key_data = _find_jwk(jwks, kid)
            if key_data is None:
                # Cached JWKS may be stale after Supabase rotates its signing
                # keys - force one re-fetch before giving up, rather than
                # failing every request for every user until this process
                # happens to restart.
                jwks = await _get_jwks(force_refresh=True)
                key_data = _find_jwk(jwks, kid)
        except jwt.InvalidTokenError:
            raise
        except Exception as e:
            # Network/parse errors from the JWKS fetch must surface as an
            # InvalidTokenError like every other auth failure here - get_current_user
            # only catches jwt.* exceptions, so anything else would otherwise
            # become an uncaught 500 instead of a clean 401 during an outage.
            raise jwt.InvalidTokenError(f"JWKS fetch failed: {e}")
        if key_data is None:
            raise jwt.InvalidTokenError("No matching key found in JWKS")
        if alg.startswith("ES"):
            public_key = ECAlgorithm.from_jwk(json.dumps(key_data))
        else:
            public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
        return jwt.decode(token, public_key, algorithms=[alg], options={"verify_aud": False})

    if not SUPABASE_JWT_SECRET:
        raise jwt.InvalidTokenError("SUPABASE_JWT_SECRET not configured")
    return jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], options={"verify_aud": False})

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    token = credentials.credentials
    try:
        return await decode_supabase_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
