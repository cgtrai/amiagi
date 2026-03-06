"""Admin bootstrap — CLI-driven first-run admin registration.

Flow
----
1. ``amiagi --admin`` (CLI)
   * Initialise the database (auto-detect SQLite / PG) and run migrations.
   * Check if any admin user already exists.  If so, abort.
   * Prompt for a Google e-mail that will become the admin account.
   * Generate a 6-digit setup code (10 min TTL).
   * Store hash(code) + email + expiry in ``admin_setup_tokens``.
   * Print instructions: start the web server, log in via Google.

2. ``/auth/callback`` (web — see ``auth_routes.py``)
   * After successful OAuth, check if the user's email has a pending
     setup token.  If so, redirect to ``/auth/setup-verify``.
   * The user enters the 6-digit code.  On match → admin role granted.
   * After 3 wrong attempts the token is blocked; the operator must
     re-run ``amiagi --admin``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from amiagi.config import Settings

logger = logging.getLogger(__name__)

_CODE_LENGTH = 6
_CODE_TTL_MINUTES = 10
_MAX_ATTEMPTS = 3


# ------------------------------------------------------------------
# Public entry point (called from ``main.py``)
# ------------------------------------------------------------------

def run_admin_bootstrap(settings: Settings) -> None:
    """Blocking entry point executed when ``amiagi --admin`` is invoked."""
    asyncio.run(_async_bootstrap(settings))


async def _async_bootstrap(settings: Settings) -> None:
    from amiagi.interfaces.web.db.pool import create_pool, run_migrations, close_pool

    print("\n╔══════════════════════════════════════════════╗")
    print("║   amiagi — Konfiguracja konta administratora  ║")
    print("╚══════════════════════════════════════════════╝\n")

    # 1. Initialise DB
    pool = await create_pool(settings)
    await run_migrations(pool, schema=settings.db_schema)
    print("  ✓ Baza danych zainicjalizowana.\n")

    try:
        # 2. Check if admin already exists
        if await _admin_exists(pool):
            print("  ⚠  Administrator jest już zarejestrowany.")
            print("     Aby dodać kolejnego, użyj panelu administracyjnego.\n")
            return

        # 3. Check for stale / unused tokens
        stale = await _count_pending_tokens(pool)
        if stale:
            answer = input(
                f"  Znaleziono {stale} niewykorzystany(ch) tokenów.\n"
                "  Czy chcesz je unieważnić i wygenerować nowy? (T/n): "
            ).strip().lower()
            if answer in ("", "t", "tak", "y", "yes"):
                await _invalidate_pending_tokens(pool)
                print("  ✓ Poprzednie tokeny unieważnione.\n")
            else:
                print("  Anulowano.\n")
                return

        # 4. Prompt for e-mail
        email = _prompt_email()
        if not email:
            print("  Anulowano.\n")
            return

        # 5. Generate setup code
        code = _generate_code()
        code_hash = _hash_code(code)

        # 6. Store in DB
        await _store_setup_token(pool, email=email, code_hash=code_hash)

        # 7. Print instructions
        print("\n  ╔═══════════════════════════════════════════════════════╗")
        print(f"  ║  Kod weryfikacyjny:  {code}                          ║")
        print(f"  ║  Ważność:            {_CODE_TTL_MINUTES} minut                          ║")
        print(f"  ║  E-mail admina:      {email:<32s} ║")
        print("  ╠═══════════════════════════════════════════════════════╣")
        print("  ║  Następne kroki:                                     ║")
        print("  ║  1. Uruchom serwer:  amiagi --ui web                 ║")
        print("  ║  2. Otwórz przeglądarkę i zaloguj się przez Google   ║")
        print("  ║  3. Podaj kod weryfikacyjny w formularzu              ║")
        print("  ╚═══════════════════════════════════════════════════════╝\n")

    finally:
        await close_pool(pool)


# ------------------------------------------------------------------
# DB helpers
# ------------------------------------------------------------------

async def _admin_exists(pool: Any) -> bool:
    """Return True if at least one user already has the admin role."""
    row = await pool.fetchval(
        """
        SELECT count(*) FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE r.name = 'admin'
        """
    )
    return (row or 0) > 0


async def _count_pending_tokens(pool: Any) -> int:
    """Count non-expired, non-used, non-blocked setup tokens."""
    now = _utc_now()
    row = await pool.fetchval(
        """
        SELECT count(*) FROM admin_setup_tokens
        WHERE is_used = $1 AND is_blocked = $2
          AND expires_at > $3
        """,
        False,
        False,
        now,
    )
    return row or 0


async def _invalidate_pending_tokens(pool: Any) -> None:
    """Block all pending setup tokens."""
    await pool.execute(
        "UPDATE admin_setup_tokens SET is_blocked = $1 WHERE is_used = $2 AND is_blocked = $3",
        True, False, False,
    )


async def _store_setup_token(pool: Any, *, email: str, code_hash: str) -> None:
    """Insert a new admin setup token."""
    expires_at = _utc_now(offset_minutes=_CODE_TTL_MINUTES)
    await pool.execute(
        """
        INSERT INTO admin_setup_tokens (email, token_hash, max_attempts, expires_at)
        VALUES ($1, $2, $3, $4)
        """,
        email,
        code_hash,
        _MAX_ATTEMPTS,
        expires_at,
    )


async def verify_setup_code(pool: Any, email: str, code: str) -> tuple[bool, str]:
    """Verify a setup code for the given email.

    Returns ``(success, message)``.
    """
    row = await pool.fetchrow(
        """
        SELECT id, token_hash, attempts, max_attempts, is_blocked, is_used, expires_at
        FROM admin_setup_tokens
        WHERE email = $1 AND is_used = $2 AND is_blocked = $3
        ORDER BY created_at DESC
        LIMIT 1
        """,
        email, False, False,
    )

    if row is None:
        return False, "Brak oczekującego tokenu dla tego adresu e-mail."

    # Check expiry (works for both SQLite text and PG timestamptz)
    expires_at_str = str(row["expires_at"])
    try:
        exp_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        if exp_dt < datetime.now(timezone.utc):
            return False, "Kod weryfikacyjny wygasł. Uruchom ponownie: amiagi --admin"
    except (ValueError, TypeError):
        pass

    if row["is_blocked"] or row["attempts"] >= row["max_attempts"]:
        return False, "Token zablokowany po zbyt wielu próbach. Uruchom ponownie: amiagi --admin"

    token_id = row["id"]
    code_hash = _hash_code(code)

    if code_hash != row["token_hash"]:
        # Increment attempts
        new_attempts = row["attempts"] + 1
        blocked = new_attempts >= row["max_attempts"]
        await pool.execute(
            "UPDATE admin_setup_tokens SET attempts = $1, is_blocked = $2 WHERE id = $3",
            new_attempts, blocked, token_id,
        )
        remaining = row["max_attempts"] - new_attempts
        if blocked:
            return False, "Nieprawidłowy kod. Token zablokowany. Uruchom ponownie: amiagi --admin"
        return False, f"Nieprawidłowy kod. Pozostało prób: {remaining}"

    # Code matches — mark token as used.
    await pool.execute(
        "UPDATE admin_setup_tokens SET is_used = $1 WHERE id = $2",
        True, token_id,
    )
    return True, "Kod zaakceptowany."


async def grant_admin_role(pool: Any, user_id: Any) -> bool:
    """Grant the admin role to *user_id*.  Returns True if granted."""
    admin_role_id = await pool.fetchval(
        "SELECT id FROM roles WHERE name = 'admin'"
    )
    if not admin_role_id:
        logger.error("Admin role not found in the database.")
        return False

    await pool.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        user_id, admin_role_id,
    )
    logger.info("Admin role granted to user %s.", user_id)
    return True


# ------------------------------------------------------------------
# Crypto helpers
# ------------------------------------------------------------------

def _generate_code() -> str:
    """Generate a cryptographically random 6-digit numeric code."""
    return "".join(str(secrets.randbelow(10)) for _ in range(_CODE_LENGTH))


def _hash_code(code: str) -> str:
    """SHA-256 hash of the code (hex digest)."""
    return hashlib.sha256(code.encode()).hexdigest()


def _utc_now(offset_minutes: int = 0) -> datetime:
    """Return a timezone-aware UTC datetime.

    Compatible with both PostgreSQL (``TIMESTAMPTZ``) and SQLite
    (aiosqlite auto-converts datetime → ISO string for TEXT columns).
    """
    dt = datetime.now(timezone.utc)
    if offset_minutes:
        dt += timedelta(minutes=offset_minutes)
    return dt


# ------------------------------------------------------------------
# Input helpers
# ------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _prompt_email() -> str | None:
    """Interactively prompt for a valid e-mail address (max 3 attempts)."""
    for attempt in range(3):
        try:
            raw = input("  Podaj adres e-mail Google dla konta admina: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if _EMAIL_RE.match(raw):
            return raw.lower()
        print(f"  ✗ Nieprawidłowy adres e-mail: {raw!r}")
    print("  Przekroczono limit prób.\n")
    return None
