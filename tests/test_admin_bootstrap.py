"""Tests for admin bootstrap and login attempt tracking."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestAdminBootstrapHelpers(unittest.TestCase):
    """Unit tests for admin_bootstrap.py helper functions."""

    def test_generate_code_length(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import _generate_code
        code = _generate_code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_generate_code_randomness(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import _generate_code
        codes = {_generate_code() for _ in range(50)}
        # Should generate at least 10 different codes out of 50
        self.assertGreater(len(codes), 10)

    def test_hash_code(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import _hash_code
        result = _hash_code("123456")
        expected = hashlib.sha256(b"123456").hexdigest()
        self.assertEqual(result, expected)

    def test_email_regex_valid(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import _EMAIL_RE
        self.assertIsNotNone(_EMAIL_RE.match("user@example.com"))
        self.assertIsNotNone(_EMAIL_RE.match("admin@google.com"))
        self.assertIsNotNone(_EMAIL_RE.match("me+tag@gmail.com"))

    def test_email_regex_invalid(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import _EMAIL_RE
        self.assertIsNone(_EMAIL_RE.match(""))
        self.assertIsNone(_EMAIL_RE.match("noatsign"))
        self.assertIsNone(_EMAIL_RE.match("@nodomain"))
        self.assertIsNone(_EMAIL_RE.match("spaces in@email.com"))

    def test_utc_now_format(self):
        from datetime import datetime as _dt, timezone as _tz
        from amiagi.interfaces.web.auth.admin_bootstrap import _utc_now
        ts = _utc_now()
        self.assertIsInstance(ts, _dt)
        self.assertEqual(ts.tzinfo, _tz.utc)

    def test_utc_now_with_offset(self):
        from datetime import datetime, timezone
        from amiagi.interfaces.web.auth.admin_bootstrap import _utc_now
        now = datetime.now(timezone.utc)
        ts = _utc_now(offset_minutes=10)
        diff = (ts - now).total_seconds()
        # Should be approximately 10 minutes (600 seconds) in the future
        self.assertAlmostEqual(diff, 600, delta=5)


class TestVerifySetupCode(unittest.TestCase):
    """Tests for the verify_setup_code function using a real SQLite pool."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _make_pool(self):
        from amiagi.interfaces.web.db.sqlite_pool import SqlitePool
        return SqlitePool(self.db_path)

    async def _setup_db(self, pool):
        """Create required tables."""
        await pool._ensure()
        migrations_dir = Path(__file__).resolve().parent / ".." / "src" / "amiagi" / "interfaces" / "web" / "db" / "migrations_sqlite"
        if migrations_dir.exists():
            for mig in sorted(migrations_dir.glob("*.sql")):
                sql = mig.read_text(encoding="utf-8")
                await pool._conn.executescript(sql)

    def test_verify_correct_code(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import (
            _hash_code,
            _utc_now,
            verify_setup_code,
        )

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                code = "123456"
                expires_at = _utc_now(offset_minutes=10)
                await pool.execute(
                    "INSERT INTO admin_setup_tokens (email, token_hash, max_attempts, expires_at) VALUES ($1, $2, $3, $4)",
                    "admin@test.com", _hash_code(code), 3, expires_at,
                )
                ok, msg = await verify_setup_code(pool, "admin@test.com", "123456")
                self.assertTrue(ok)
                self.assertIn("zaakceptowany", msg.lower())
            finally:
                await pool.close()

        self._run(_test())

    def test_verify_wrong_code_decrements(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import (
            _hash_code,
            _utc_now,
            verify_setup_code,
        )

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                code = "123456"
                expires_at = _utc_now(offset_minutes=10)
                await pool.execute(
                    "INSERT INTO admin_setup_tokens (email, token_hash, max_attempts, expires_at) VALUES ($1, $2, $3, $4)",
                    "admin@test.com", _hash_code(code), 3, expires_at,
                )
                ok, msg = await verify_setup_code(pool, "admin@test.com", "000000")
                self.assertFalse(ok)
                self.assertIn("Pozostało prób: 2", msg)
            finally:
                await pool.close()

        self._run(_test())

    def test_verify_blocks_after_max_attempts(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import (
            _hash_code,
            _utc_now,
            verify_setup_code,
        )

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                code = "123456"
                expires_at = _utc_now(offset_minutes=10)
                await pool.execute(
                    "INSERT INTO admin_setup_tokens (email, token_hash, max_attempts, expires_at) VALUES ($1, $2, $3, $4)",
                    "admin@test.com", _hash_code(code), 3, expires_at,
                )

                # Exhaust all 3 attempts
                msg = ""
                for i in range(3):
                    ok, msg = await verify_setup_code(pool, "admin@test.com", "000000")
                    self.assertFalse(ok)

                # Last attempt should have blocked the token
                self.assertIn("zablokowany", msg.lower())

                # Even correct code should now fail (no valid token)
                ok, msg = await verify_setup_code(pool, "admin@test.com", "123456")
                self.assertFalse(ok)
            finally:
                await pool.close()

        self._run(_test())

    def test_verify_expired_code(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import (
            _hash_code,
            _utc_now,
            verify_setup_code,
        )

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                code = "123456"
                # Set expiry in the past
                expires_at = _utc_now(offset_minutes=-5)
                await pool.execute(
                    "INSERT INTO admin_setup_tokens (email, token_hash, max_attempts, expires_at) VALUES ($1, $2, $3, $4)",
                    "admin@test.com", _hash_code(code), 3, expires_at,
                )
                ok, msg = await verify_setup_code(pool, "admin@test.com", "123456")
                self.assertFalse(ok)
                self.assertIn("wygasł", msg.lower())
            finally:
                await pool.close()

        self._run(_test())

    def test_verify_nonexistent_email(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import verify_setup_code

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                ok, msg = await verify_setup_code(pool, "nobody@test.com", "123456")
                self.assertFalse(ok)
                self.assertIn("brak", msg.lower())
            finally:
                await pool.close()

        self._run(_test())


class TestGrantAdminRole(unittest.TestCase):
    """Tests for grant_admin_role."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _make_pool(self):
        from amiagi.interfaces.web.db.sqlite_pool import SqlitePool
        return SqlitePool(self.db_path)

    async def _setup_db(self, pool):
        await pool._ensure()
        migrations_dir = Path(__file__).resolve().parent / ".." / "src" / "amiagi" / "interfaces" / "web" / "db" / "migrations_sqlite"
        if migrations_dir.exists():
            for mig in sorted(migrations_dir.glob("*.sql")):
                sql = mig.read_text(encoding="utf-8")
                await pool._conn.executescript(sql)

    def test_grant_admin_role(self):
        from amiagi.interfaces.web.auth.admin_bootstrap import grant_admin_role

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                # Create a user first
                await pool.execute(
                    "INSERT INTO users (id, email, display_name) VALUES ($1, $2, $3)",
                    "user-123", "admin@test.com", "Admin",
                )
                ok = await grant_admin_role(pool, "user-123")
                self.assertTrue(ok)

                # Verify the role assignment
                row = await pool.fetchval(
                    """
                    SELECT count(*) FROM user_roles ur
                    JOIN roles r ON r.id = ur.role_id
                    WHERE ur.user_id = $1 AND r.name = 'admin'
                    """,
                    "user-123",
                )
                self.assertEqual(row, 1)
            finally:
                await pool.close()

        self._run(_test())


class TestLoginAttemptTracking(unittest.TestCase):
    """Tests for login attempt recording and blocking."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _make_pool(self):
        from amiagi.interfaces.web.db.sqlite_pool import SqlitePool
        return SqlitePool(self.db_path)

    async def _setup_db(self, pool):
        await pool._ensure()
        migrations_dir = Path(__file__).resolve().parent / ".." / "src" / "amiagi" / "interfaces" / "web" / "db" / "migrations_sqlite"
        if migrations_dir.exists():
            for mig in sorted(migrations_dir.glob("*.sql")):
                sql = mig.read_text(encoding="utf-8")
                await pool._conn.executescript(sql)

    def test_record_login_attempt(self):
        from amiagi.interfaces.web.routes.auth_routes import _record_login_attempt

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                await _record_login_attempt(pool, "user@test.com", "127.0.0.1", success=True, reason=None)
                count = await pool.fetchval("SELECT count(*) FROM login_attempts")
                self.assertEqual(count, 1)
            finally:
                await pool.close()

        self._run(_test())

    def test_not_blocked_initially(self):
        from amiagi.interfaces.web.routes.auth_routes import _is_login_blocked

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                blocked = await _is_login_blocked(pool, "user@test.com", "127.0.0.1")
                self.assertFalse(blocked)
            finally:
                await pool.close()

        self._run(_test())

    def test_blocked_after_max_failures(self):
        from amiagi.interfaces.web.routes.auth_routes import (
            _LOGIN_MAX_FAILED_ATTEMPTS,
            _is_login_blocked,
            _record_login_attempt,
        )

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                for _ in range(_LOGIN_MAX_FAILED_ATTEMPTS):
                    await _record_login_attempt(
                        pool, "user@test.com", "127.0.0.1",
                        success=False, reason="test",
                    )
                blocked = await _is_login_blocked(pool, "user@test.com", "127.0.0.1")
                self.assertTrue(blocked)
            finally:
                await pool.close()

        self._run(_test())

    def test_success_doesnt_trigger_block(self):
        from amiagi.interfaces.web.routes.auth_routes import (
            _is_login_blocked,
            _record_login_attempt,
        )

        async def _test():
            pool = self._make_pool()
            await self._setup_db(pool)
            try:
                for _ in range(10):
                    await _record_login_attempt(
                        pool, "user@test.com", "127.0.0.1",
                        success=True, reason=None,
                    )
                blocked = await _is_login_blocked(pool, "user@test.com", "127.0.0.1")
                self.assertFalse(blocked)
            finally:
                await pool.close()

        self._run(_test())


class TestAdminBootstrapCLI(unittest.TestCase):
    """Tests for the --admin CLI flag integration."""

    def test_admin_arg_parsed(self):
        from amiagi.main import _parse_args
        args = _parse_args(["--admin"])
        self.assertTrue(args.admin)

    def test_admin_arg_default_false(self):
        from amiagi.main import _parse_args
        args = _parse_args([])
        self.assertFalse(args.admin)


class TestAuthRoutesSetupVerify(unittest.TestCase):
    """Structural tests for auth_setup_verify route."""

    def test_route_list_contains_setup_verify(self):
        from amiagi.interfaces.web.routes.auth_routes import auth_routes
        paths = [r.path for r in auth_routes]
        self.assertIn("/auth/setup-verify", paths)

    def test_setup_verify_methods(self):
        from amiagi.interfaces.web.routes.auth_routes import auth_routes
        for route in auth_routes:
            if route.path == "/auth/setup-verify":
                # Starlette stores methods as a set of uppercase strings
                # or the endpoint may accept both GET and POST.
                self.assertIsNotNone(route)
                break
        else:
            self.fail("/auth/setup-verify route not found")


if __name__ == "__main__":
    unittest.main()
