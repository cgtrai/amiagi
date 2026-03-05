"""Tests for JWT session manager — create, validate, revoke."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import jwt
import pytest

from amiagi.interfaces.web.auth.session import SessionManager, UserSession, _JWT_ALGORITHM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-key-for-unit-tests!!"  # ≥32 bytes for HS256
_USER_ID = uuid4()
_SESSION_ID = uuid4()


class _FakePool:
    """Minimal asyncpg.Pool mock that supports ``acquire()`` as an async context manager."""

    def __init__(self):
        self.conn = _FakeConnection()

    def acquire(self):
        return _AcquireCtx(self.conn)


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


class _FakeConnection:
    """Records SQL calls and returns pre-programmed values."""

    def __init__(self):
        self.executed: list[tuple] = []
        self._fetchrow_result = None
        self._fetch_results: list[list] = [[], []]
        self._fetchval_result = None
        self._fetch_call_idx = 0

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "UPDATE 1"

    async def fetchrow(self, query, *args):
        self.executed.append((query, args))
        return self._fetchrow_result

    async def fetch(self, query, *args):
        self.executed.append((query, args))
        idx = self._fetch_call_idx
        self._fetch_call_idx += 1
        if idx < len(self._fetch_results):
            return self._fetch_results[idx]
        return []

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        return self._fetchval_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def pool():
    return _FakePool()


@pytest.fixture()
def manager(pool):
    return SessionManager(secret_key=_SECRET, pool=pool, session_hours=1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreateSession:
    @pytest.mark.asyncio
    async def test_returns_jwt_string(self, manager):
        token = await manager.create_session(_USER_ID)
        assert isinstance(token, str)
        assert len(token) > 50

    @pytest.mark.asyncio
    async def test_jwt_contains_sub(self, manager):
        token = await manager.create_session(_USER_ID)
        payload = jwt.decode(token, _SECRET, algorithms=[_JWT_ALGORITHM])
        assert payload["sub"] == str(_USER_ID)

    @pytest.mark.asyncio
    async def test_jwt_has_exp(self, manager):
        token = await manager.create_session(_USER_ID)
        payload = jwt.decode(token, _SECRET, algorithms=[_JWT_ALGORITHM])
        assert "exp" in payload

    @pytest.mark.asyncio
    async def test_insert_session_in_db(self, manager, pool):
        await manager.create_session(_USER_ID)
        assert any("INSERT INTO sessions" in q[0] for q in pool.conn.executed)

    @pytest.mark.asyncio
    async def test_token_hash_stored(self, manager, pool):
        token = await manager.create_session(_USER_ID)
        expected_hash = hashlib.sha256(token.encode()).hexdigest()
        # The second arg to the INSERT should be the hash
        insert_call = [q for q in pool.conn.executed if "INSERT INTO sessions" in q[0]][0]
        assert insert_call[1][1] == expected_hash


class TestValidateSession:
    @pytest.mark.asyncio
    async def test_valid_token(self, manager, pool):
        pool.conn._fetchrow_result = {
            "session_id": _SESSION_ID,
            "email": "test@example.com",
            "display_name": "Test User",
            "avatar_url": None,
            "is_active": True,
            "is_blocked": False,
        }
        pool.conn._fetch_results = [
            [{"name": "admin"}],
            [{"codename": "agents.view"}],
        ]
        token = await manager.create_session(_USER_ID)
        # Reset fetch index after create
        pool.conn._fetch_call_idx = 0
        result = await manager.validate_session(token)
        assert result is not None
        assert isinstance(result, UserSession)
        assert result.email == "test@example.com"

    @pytest.mark.asyncio
    async def test_expired_token(self, manager, pool):
        # Create an already-expired token
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(_USER_ID),
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        }
        token = jwt.encode(payload, _SECRET, algorithm=_JWT_ALGORITHM)
        result = await manager.validate_session(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_token(self, manager, pool):
        result = await manager.validate_session("not-a-jwt")
        assert result is None

    @pytest.mark.asyncio
    async def test_blocked_user(self, manager, pool):
        pool.conn._fetchrow_result = {
            "session_id": _SESSION_ID,
            "email": "blocked@example.com",
            "display_name": "Blocked",
            "avatar_url": None,
            "is_active": True,
            "is_blocked": True,
        }
        token = await manager.create_session(_USER_ID)
        result = await manager.validate_session(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_inactive_user(self, manager, pool):
        pool.conn._fetchrow_result = {
            "session_id": _SESSION_ID,
            "email": "inactive@example.com",
            "display_name": "Inactive",
            "avatar_url": None,
            "is_active": False,
            "is_blocked": False,
        }
        token = await manager.create_session(_USER_ID)
        result = await manager.validate_session(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_session_in_db(self, manager, pool):
        pool.conn._fetchrow_result = None
        token = await manager.create_session(_USER_ID)
        result = await manager.validate_session(token)
        assert result is None


class TestRevokeSession:
    @pytest.mark.asyncio
    async def test_revoke_session(self, manager, pool):
        await manager.revoke_session(_SESSION_ID)
        assert any("UPDATE sessions SET revoked" in q[0] for q in pool.conn.executed)

    @pytest.mark.asyncio
    async def test_revoke_all(self, manager, pool):
        count = await manager.revoke_all_user_sessions(_USER_ID)
        assert any("UPDATE sessions SET revoked" in q[0] for q in pool.conn.executed)
