"""Credentials: API keys, plain bearer tokens, and refreshable OAuth tokens (AUTH.md).

* API key (``ak_…`` or legacy ``horus_pat_…``): sent as ``Authorization: Bearer``.
* OAuth access token from Horus: same header. With a refresh token and ``client_id``
  the SDK refreshes it shortly before ``expires_at`` and once after a ``401``
  (e.g. ``invalid_access_token``), stores the rotated refresh token (Horus rotates on every
  use, and a reused one ends the grant) and calls ``on_refresh`` so you can persist it.
* The token endpoint is never hard-coded: pass ``token_endpoint``, or it is discovered
  from ``<iss>/.well-known/openid-configuration`` using the access token's ``iss`` claim.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

import httpx

from ._exceptions import AuthenticationError, HorusError

__all__ = ["OAuthCredentials", "TokenRefreshError"]

REFRESH_LEEWAY_SECONDS = 60.0


class TokenRefreshError(HorusError):
    """Refreshing the OAuth access token failed (sign in again)."""


@dataclass
class OAuthCredentials:
    """An Horus OAuth grant. Mutated in place when refreshed."""

    access_token: str
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    expires_at: Optional[float] = None
    """Unix seconds; derived from the token's ``exp`` claim when omitted."""
    token_endpoint: Optional[str] = None
    scope: Optional[str] = None
    on_refresh: Optional[Callable[[OAuthCredentials], None]] = field(default=None, repr=False, compare=False)
    """Called after every refresh with the updated credentials (persist the new refresh token)."""

    def __post_init__(self) -> None:
        if self.expires_at is None:
            self.expires_at = _exp_claim(self.access_token)

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token and self.client_id)

    def expiring(self, now: Optional[float] = None) -> bool:
        """True when the access token expires within the refresh leeway."""
        if self.expires_at is None:
            return False
        return (time.time() if now is None else now) >= self.expires_at - REFRESH_LEEWAY_SECONDS

    def issuer(self) -> Optional[str]:
        iss = _jwt_claims(self.access_token).get("iss")
        return iss.rstrip("/") if isinstance(iss, str) and iss else None


def _exp_claim(token: str) -> Optional[float]:
    exp = _jwt_claims(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def _jwt_claims(token: str) -> Dict[str, object]:
    """The (unverified) payload of a JWT, or ``{}`` for anything else."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


class Auth:
    """What the clients use: the current header value, and refresh plumbing for OAuth."""

    def __init__(self, token: Optional[str] = None, oauth: Optional[OAuthCredentials] = None) -> None:
        if (token is None) == (oauth is None):
            raise ValueError("pass exactly one of token / oauth")
        self._token = token
        self.oauth = oauth
        self.lock = threading.Lock()

    @property
    def header(self) -> str:
        return f"Bearer {self.oauth.access_token if self.oauth else self._token}"

    @property
    def refreshable(self) -> bool:
        return self.oauth is not None and self.oauth.can_refresh

    def needs_refresh(self) -> bool:
        return self.oauth is not None and self.oauth.can_refresh and self.oauth.expiring()

    def refresh_form(self) -> Dict[str, str]:
        creds = self._require_oauth()
        return {
            "grant_type": "refresh_token",
            "refresh_token": creds.refresh_token or "",
            "client_id": creds.client_id or "",
        }

    def discovery_url(self) -> str:
        issuer = self._require_oauth().issuer()
        if not issuer:
            raise TokenRefreshError("cannot refresh: no token_endpoint given and the access token has no `iss` claim")
        return f"{issuer}/.well-known/openid-configuration"

    def apply_discovery(self, response: httpx.Response) -> str:
        creds = self._require_oauth()
        endpoint = _json_field(response, "token_endpoint")
        if not endpoint:
            raise TokenRefreshError(f"OAuth discovery failed ({response.status_code})")
        creds.token_endpoint = endpoint
        return endpoint

    def apply_token_response(self, response: httpx.Response) -> None:
        """Store a refreshed token (and the rotated refresh token), then notify."""
        creds = self._require_oauth()
        access = _json_field(response, "access_token")
        if response.status_code != 200 or not access:
            error = _json_field(response, "error") or f"http_{response.status_code}"
            raise TokenRefreshError(f"token refresh failed: {error}")
        creds.access_token = access
        creds.refresh_token = _json_field(response, "refresh_token") or creds.refresh_token
        creds.scope = _json_field(response, "scope") or creds.scope
        creds.expires_at = _expires_at(response) or _exp_claim(access)
        if creds.on_refresh is not None:
            creds.on_refresh(creds)

    def _require_oauth(self) -> OAuthCredentials:
        if self.oauth is None:
            raise TokenRefreshError("not an OAuth credential")
        return self.oauth


# 401s a refreshed token cannot fix. Anything else (``invalid_access_token`` per AUTH.md;
# production currently answers ``invalid_credentials``) is worth one refresh.
_NOT_FIXED_BY_REFRESH = frozenset({"account_not_linked", "missing_credentials", "device_revoked"})


def is_expired_token_error(error: AuthenticationError) -> bool:
    return error.code not in _NOT_FIXED_BY_REFRESH


def _json_field(response: httpx.Response, name: str) -> Optional[str]:
    try:
        body = response.json()
    except ValueError:
        return None
    value = body.get(name) if isinstance(body, dict) else None
    return value if isinstance(value, str) and value else None


def _expires_at(response: httpx.Response) -> Optional[float]:
    try:
        expires_in = response.json().get("expires_in")
    except (ValueError, AttributeError):
        return None
    return time.time() + float(expires_in) if isinstance(expires_in, (int, float)) else None
