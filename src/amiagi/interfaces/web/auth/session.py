"""JWT session manager — create, validate, revoke sessions."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

import jwt

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# Default session lifetime
_DEFAULT_SESSION_HOURS = 24

# JWT algorithm
_JWT_ALGORITHM = "HS256"


@dataclass(frozen=True)
class UserSession:
    """Lightweight session object attached to ``request.state.user``."""

    user_id: UUID
    session_id: UUID
    email: str
    display_name: str
    avatar_url: str | None = None
    roles: list[str] | None = None
    permissions: list[str] | None = None


class SessionManager:
    """JWT-based session management backed by PostgreSQL."""

    def __init__(
        self,
        secret_key: str,
        pool: "asyncpg.Pool",
        *,
        session_hours: int = _DEFAULT_SESSION_HOURS,
    ) -> None:
        self._secret = secret_key
        self._pool = pool
        self._session_hours = session_hours

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_session(
        self,
        user_id: UUID,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        """Create a new session and return a signed JWT token."""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=self._session_hours)

        payload = {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        token = jwt.encode(payload, self._secret, algorithm=_JWT_ALGORITHM)
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (user_id, token_hash, ip_address, user_agent, expires_at)
                VALUES ($1, $2, $3, $4, $5)
                """,
                user_id,
                token_hash,
                ip_address,
                user_agent,
                expires_at,
            )

        logger.info("Session created for user %s (expires %s)", user_id, expires_at)
        return token

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    async def validate_session(self, token: str) -> UserSession | None:
        """Decode and validate a JWT token against the DB session store."""
        try:
            payload = jwt.decode(token, self._secret, algorithms=[_JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            logger.debug("JWT expired.")
            return None
        except jwt.InvalidTokenError:
            logger.debug("Invalid JWT token.")
            return None

        user_id = UUID(payload["sub"])
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT s.id AS session_id, u.email, u.display_name, u.avatar_url,
                       u.is_active, u.is_blocked
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.user_id = $1
                  AND s.token_hash = $2
                  AND s.revoked = false
                  AND s.expires_at > now()
                """,
                user_id,
                token_hash,
            )

        if row is None:
            return None

        if not row["is_active"] or row["is_blocked"]:
            return None

        # Fetch roles and permissions
        roles, permissions = await self._load_roles_and_permissions(user_id)

        return UserSession(
            user_id=user_id,
            session_id=row["session_id"],
            email=row["email"],
            display_name=row["display_name"],
            avatar_url=row["avatar_url"],
            roles=roles,
            permissions=permissions,
        )

    # ------------------------------------------------------------------
    # Revoke
    # ------------------------------------------------------------------

    async def revoke_session(self, session_id: UUID) -> None:
        """Revoke a single session."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET revoked = true WHERE id = $1",
                session_id,
            )
        logger.info("Session %s revoked.", session_id)

    async def revoke_all_user_sessions(self, user_id: UUID) -> int:
        """Revoke all active sessions for a user.  Returns count revoked."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE sessions SET revoked = true WHERE user_id = $1 AND revoked = false",
                user_id,
            )
        count = int(result.split()[-1]) if result else 0
        logger.info("Revoked %d sessions for user %s.", count, user_id)
        return count

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_roles_and_permissions(self, user_id: UUID) -> tuple[list[str], list[str]]:
        """Load role names and permission codenames for a user."""
        async with self._pool.acquire() as conn:
            role_rows = await conn.fetch(
                """
                SELECT r.name
                FROM user_roles ur
                JOIN roles r ON r.id = ur.role_id
                WHERE ur.user_id = $1
                """,
                user_id,
            )
            roles = [r["name"] for r in role_rows]

            perm_rows = await conn.fetch(
                """
                SELECT DISTINCT p.codename
                FROM user_roles ur
                JOIN role_permissions rp ON rp.role_id = ur.role_id
                JOIN permissions p ON p.id = rp.permission_id
                WHERE ur.user_id = $1
                """,
                user_id,
            )
            permissions = [p["codename"] for p in perm_rows]

        return roles, permissions
