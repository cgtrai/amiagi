"""Authentication routes — /auth/login, /auth/callback, /auth/logout, /auth/setup-verify."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

_SESSION_COOKIE = "amiagi_session"
_PENDING_ADMIN_COOKIE = "amiagi_pending_admin"

# Login-attempt rate-limiting constants
_LOGIN_ATTEMPT_WINDOW_MINUTES = 15
_LOGIN_MAX_FAILED_ATTEMPTS = 5


# ------------------------------------------------------------------
# GET /auth/login
# ------------------------------------------------------------------

async def auth_login(request: Request) -> Response:
    """Render the login page with a Google sign-in button."""
    from amiagi.interfaces.web.auth.oauth import build_authorize_url, generate_state_token

    settings = request.app.state.settings
    # HMAC-signed state — verifiable from the URL alone, no cookie needed.
    state = generate_state_token(secret=settings.oauth_client_secret)
    login_url = build_authorize_url(settings, state)

    error_message = request.query_params.get("error", "")

    templates = getattr(request.app.state, "templates", None)
    if templates is not None:
        response = templates.TemplateResponse(
            request,
            "login.html",
            {"login_url": login_url, "error_message": error_message},
        )
    else:
        # Fallback: direct redirect (used when templates not loaded, e.g. tests)
        response = RedirectResponse(url=login_url, status_code=302)

    return response


# ------------------------------------------------------------------
# GET /auth/callback
# ------------------------------------------------------------------

async def auth_callback(request: Request) -> Response:
    """Handle Google OAuth 2.0 callback — upsert user, create session."""
    from amiagi.interfaces.web.auth.oauth import (
        exchange_code_for_tokens,
        fetch_userinfo,
        verify_state_token,
    )

    settings = request.app.state.settings

    code = request.query_params.get("code")
    state_received = request.query_params.get("state", "")

    # Validate CSRF state via HMAC signature (no cookie needed).
    if not code or not verify_state_token(state_received, settings.oauth_client_secret):
        logger.warning(
            "OAuth state validation failed: state=%s",
            state_received[:20] + "..." if len(state_received) > 20 else state_received,
        )
        return JSONResponse({"error": "invalid_state"}, status_code=400)

    pool = request.app.state.db_pool

    # Exchange code → tokens → userinfo
    try:
        tokens = await exchange_code_for_tokens(settings, code)
        access_token = tokens.get("access_token", "")
        userinfo = await fetch_userinfo(access_token)
    except Exception as exc:
        logger.error("OAuth token exchange failed: %s", exc)
        return JSONResponse({"error": "token_exchange_failed"}, status_code=502)

    email = userinfo.get("email", "")
    display_name = userinfo.get("name", email)
    avatar_url = userinfo.get("picture")
    provider_sub = userinfo.get("sub", "")

    if not email:
        return JSONResponse({"error": "no_email"}, status_code=400)

    ip = request.client.host if request.client else None

    # ── Login attempt rate-limiting ──────────────────────────
    if await _is_login_blocked(pool, email, ip):
        await _record_login_attempt(pool, email, ip, success=False, reason="rate_limited")
        return _render_login_error(
            request,
            f"Zbyt wiele nieudanych prób logowania. Spróbuj ponownie za {_LOGIN_ATTEMPT_WINDOW_MINUTES} minut.",
        )

    # ── User upsert + session (all DB ops wrapped) ───────────
    try:
        # Upsert user
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (email, display_name, avatar_url, provider, provider_sub)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (email)
                DO UPDATE SET display_name = EXCLUDED.display_name,
                              avatar_url   = EXCLUDED.avatar_url,
                              provider_sub = EXCLUDED.provider_sub,
                              updated_at   = now()
                RETURNING id, is_active, is_blocked
                """,
                email,
                display_name,
                avatar_url,
                settings.oauth_provider,
                provider_sub,
            )

        user_id: UUID = row["id"]

        # Check if blocked
        if row["is_blocked"]:
            await _record_login_attempt(pool, email, ip, success=False, reason="account_blocked")
            return _render_login_error(request, "Konto zostało zablokowane.")

        if not row["is_active"]:
            await _record_login_attempt(pool, email, ip, success=False, reason="account_inactive")
            return _render_login_error(request, "Konto jest nieaktywne.")

        # ── Admin setup token flow ───────────────────────────
        # If this email has a pending admin setup token, redirect to the
        # verification form *before* creating a full session.
        has_pending = await _has_pending_admin_token(pool, email)
        if has_pending:
            # Store user_id temporarily so the verify route can use it.
            response = RedirectResponse(url="/auth/setup-verify", status_code=302)
            response.set_cookie(
                _PENDING_ADMIN_COOKIE,
                f"{user_id}|{email}",
                max_age=600,  # 10 min
                httponly=True,
                samesite="lax",
            )
            return response

        # First-ever user → auto-assign admin role (legacy fallback)
        await _auto_assign_admin_if_first(pool, user_id)

        # Successful login — record and proceed.
        await _record_login_attempt(pool, email, ip, success=True, reason=None)

        # Auto-provision workspace for this user
        workspace_mgr = getattr(request.app.state, "workspace_manager", None)
        if workspace_mgr is not None:
            workspace_mgr.ensure_workspace(str(user_id))

        # Create session
        session_manager = request.app.state.session_manager
        ua = request.headers.get("user-agent")
        token = await session_manager.create_session(user_id, ip_address=ip, user_agent=ua)
    except Exception as exc:
        logger.error("OAuth callback DB/session error for %s: %s", email, exc, exc_info=True)
        return _render_login_error(request, "Wystąpił błąd serwera. Spróbuj ponownie.")

    # Log login activity
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is not None:
        try:
            await activity_logger.log(
                user_id=str(user_id),
                action="user.login",
                detail={"email": email, "provider": settings.oauth_provider},
                ip_address=ip,
            )
        except Exception as exc:
            logger.warning("Activity logging failed on login: %s", exc)

    # Set cookie & redirect
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=86400,  # 24h
        httponly=True,
        samesite="lax",
        secure=False,  # switch to True with HTTPS
    )
    return response


# ------------------------------------------------------------------
# GET /auth/logout
# ------------------------------------------------------------------

async def auth_logout(request: Request) -> Response:
    """Revoke session and clear cookie."""
    session_manager = request.app.state.session_manager
    user = getattr(request.state, "user", None)
    if user is not None:
        await session_manager.revoke_session(user.session_id)

        # Log logout activity
        activity_logger = getattr(request.app.state, "activity_logger", None)
        if activity_logger is not None:
            try:
                ip = request.client.host if request.client else None
                await activity_logger.log(
                    user_id=str(user.id),
                    action="user.logout",
                    ip_address=ip,
                )
            except Exception as exc:
                logger.warning("Activity logging failed on logout: %s", exc)

    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie(_SESSION_COOKIE)
    return response


# ------------------------------------------------------------------
# GET/POST /auth/setup-verify
# ------------------------------------------------------------------

async def auth_setup_verify(request: Request) -> Response:
    """Render or process the admin setup code verification form."""
    from amiagi.interfaces.web.auth.admin_bootstrap import grant_admin_role, verify_setup_code

    pending_raw = request.cookies.get(_PENDING_ADMIN_COOKIE, "")
    if "|" not in pending_raw:
        return _render_login_error(request, "Brak oczekującej sesji rejestracji admina.")

    user_id_str, email = pending_raw.split("|", 1)
    pool = request.app.state.db_pool

    templates = getattr(request.app.state, "templates", None)

    if request.method == "GET":
        if templates is not None:
            return templates.TemplateResponse(
                request,
                "admin_setup_verify.html",
                {"error_message": ""},
            )
        return HTMLResponse("<form method='POST'><input name='code'/><button>OK</button></form>")

    # POST — validate code
    form = await request.form()
    code = str(form.get("code", "")).strip()

    ok, msg = await verify_setup_code(pool, email, code)
    if not ok:
        # Compute remaining attempts for the template
        remaining = None
        import re
        m = re.search(r"Pozostało prób: (\d+)", msg)
        if m:
            remaining = int(m.group(1))

        if templates is not None:
            return templates.TemplateResponse(
                request,
                "admin_setup_verify.html",
                {"error_message": msg, "remaining": remaining},
            )
        return HTMLResponse(f"<p>{msg}</p>", status_code=400)

    # Code verified — grant admin role
    granted = await grant_admin_role(pool, user_id_str)
    if not granted:
        return _render_login_error(request, "Nie udało się przypisać roli admina.")

    ip = request.client.host if request.client else None
    await _record_login_attempt(pool, email, ip, success=True, reason="admin_bootstrap")

    # Create session now
    session_manager = request.app.state.session_manager
    ua = request.headers.get("user-agent")
    token = await session_manager.create_session(user_id_str, ip_address=ip, user_agent=ua)

    # Log activity
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is not None:
        try:
            await activity_logger.log(
                user_id=user_id_str,
                action="admin.bootstrap_complete",
                detail={"email": email},
                ip_address=ip,
            )
        except Exception as exc:
            logger.warning("Activity logging failed on admin bootstrap: %s", exc)

    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=86400,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    response.delete_cookie(_PENDING_ADMIN_COOKIE)
    return response


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _has_pending_admin_token(pool, email: str) -> bool:
    """Check if *email* has a pending (non-expired, non-used, non-blocked) setup token."""
    now = datetime.now(timezone.utc)
    row = await pool.fetchval(
        """
        SELECT count(*) FROM admin_setup_tokens
        WHERE email = $1 AND is_used = $2 AND is_blocked = $3
          AND expires_at > $4
        """,
        email, False, False, now,
    )
    return (row or 0) > 0


async def _auto_assign_admin_if_first(pool, user_id: UUID) -> None:
    """Assign admin role to the very first user (empty user_roles table)."""
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM user_roles")
        if count == 0:
            admin_role_id = await conn.fetchval(
                "SELECT id FROM roles WHERE name = 'admin'"
            )
            if admin_role_id:
                await conn.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    user_id,
                    admin_role_id,
                )
                logger.info("First user %s auto-assigned admin role.", user_id)


# ── Login attempt tracking ───────────────────────────────────

async def _record_login_attempt(
    pool, email: str, ip: str | None, *, success: bool, reason: str | None,
) -> None:
    """Insert a login attempt record."""
    try:
        await pool.execute(
            """
            INSERT INTO login_attempts (email, ip_address, success, reason)
            VALUES ($1, $2, $3, $4)
            """,
            email, ip, success, reason,
        )
    except Exception as exc:
        # Non-critical — don't break the login flow.
        logger.warning("Failed to record login attempt: %s", exc)


async def _is_login_blocked(pool, email: str, ip: str | None) -> bool:
    """Return True if *email* has exceeded the failed-attempt threshold."""
    try:
        cutoff = _utc_cutoff(_LOGIN_ATTEMPT_WINDOW_MINUTES)
        count = await pool.fetchval(
            """
            SELECT count(*) FROM login_attempts
            WHERE email = $1
              AND success = $2
              AND created_at > $3
            """,
            email, False, cutoff,
        )
        return (count or 0) >= _LOGIN_MAX_FAILED_ATTEMPTS
    except Exception as exc:
        logger.warning("Login attempt check failed: %s", exc)
        return False  # fail-open — don't lock users out on DB errors


def _utc_cutoff(minutes: int) -> datetime:
    """Return a timezone-aware UTC datetime *minutes* ago."""
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _render_login_error(request: Request, message: str) -> Response:
    """Redirect back to login with an error query parameter."""
    from urllib.parse import quote
    return RedirectResponse(url=f"/auth/login?error={quote(message)}", status_code=302)


# ------------------------------------------------------------------
# Route list
# ------------------------------------------------------------------

auth_routes = [
    Route("/auth/login", auth_login, methods=["GET"]),
    Route("/auth/callback", auth_callback, methods=["GET"]),
    Route("/auth/logout", auth_logout, methods=["GET"]),
    Route("/auth/setup-verify", auth_setup_verify, methods=["GET", "POST"]),
]
