import time
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings

security = HTTPBearer()

_jwks_cache: dict | None = None
_jwks_cache_ts: float = 0
_JWKS_CACHE_TTL = 24 * 60 * 60  # 24 hours

ROLE_SKILL_ADMIN = "SkillAdmin"
ROLE_SKILL_USER = "SkillUser"


@dataclass
class UserInfo:
    oid: str
    tenant_id: str
    name: str
    email: str
    roles: list[str] = field(default_factory=list)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
async def _fetch_jwks_raw(jwks_url: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        return resp.json()


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_cache_ts
    now = time.time()
    if _jwks_cache is not None and (now - _jwks_cache_ts) < _JWKS_CACHE_TTL:
        return _jwks_cache

    jwks_url = (
        f"https://login.microsoftonline.com/{settings.azure_ad_tenant_id}"
        f"/discovery/v2.0/keys"
    )
    _jwks_cache = await _fetch_jwks_raw(jwks_url)
    _jwks_cache_ts = now
    return _jwks_cache


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UserInfo:
    token = credentials.credentials

    try:
        jwks = await _get_jwks()
        unverified_header = jwt.get_unverified_header(token)
        rsa_key = {}
        for key in jwks.get("keys", []):
            if key["kid"] == unverified_header.get("kid"):
                rsa_key = key
                break

        if not rsa_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to find appropriate key",
            )

        # Azure AD v2.0 tokens may have audience as "api://<id>" or bare "<id>"
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
            issuer=f"https://login.microsoftonline.com/{settings.azure_ad_tenant_id}/v2.0",
        )
        token_aud = payload.get("aud", "")
        valid_audiences = {settings.azure_ad_audience, settings.azure_ad_client_id}
        if token_aud not in valid_audiences:
            raise JWTError(f"Invalid audience: {token_aud}")

        tenant_id = payload.get("tid", "")
        if not tenant_id:
            raise JWTError("Missing tenant ID (tid) in token")

        return UserInfo(
            oid=payload["oid"],
            tenant_id=tenant_id,
            name=payload.get("name", ""),
            email=payload.get("preferred_username", ""),
            roles=payload.get("roles", []),
        )

    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {e}",
        )


def require_role(*allowed_roles: str):
    """FastAPI dependency factory that checks the user has at least one of the allowed roles."""

    async def _check(
        user: UserInfo = Depends(get_current_user),
    ) -> UserInfo:
        if not any(r in user.roles for r in allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _check


require_admin = require_role(ROLE_SKILL_ADMIN)
require_any_role = require_role(ROLE_SKILL_ADMIN, ROLE_SKILL_USER)
