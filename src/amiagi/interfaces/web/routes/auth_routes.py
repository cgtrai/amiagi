"""Authentication routes — /auth/login, /auth/callback, /auth/logout."""

from __future__ import annotations

import logging
from uuid import UUID

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

_SESSION_COOKIE = "amiagi_session"


# ------------------------------------------------------------------
# GET /auth/login
# ------------------------------------------------------------------

async def auth_login(request: Request) -> Response:
    """Render the login page with a Google sign-in button."""
    from amiagi.interfaces.web.auth.oauth import build_authorize_url, generate_state_token

    settings = request.app.state.settings
    state = generate_state_token()
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

    response.set_cookie(
        "oauth_state",
        state,
        max_age=600,  # 10 minutes
        httponly=True,
        samesite="lax",
    )
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
    state_expected = request.cookies.get("oauth_state", "")

    # Validate CSRF state
    if not code or not verify_state_token(state_received, state_expected):
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
        return _render_login_error(request, "Konto zostało zablokowane.")

    if not row["is_active"]:
        return _render_login_error(request, "Konto jest nieaktywne.")

    # First-ever user → auto-assign admin role
    await _auto_assign_admin_if_first(pool, user_id)

    # Auto-provision workspace for this user
    workspace_mgr = getattr(request.app.state, "workspace_manager", None)
    if workspace_mgr is not None:
        workspace_mgr.ensure_workspace(str(user_id))

    # Create session
    session_manager = request.app.state.session_manager
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    token = await session_manager.create_session(user_id, ip_address=ip, user_agent=ua)

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
    response.delete_cookie("oauth_state")
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
# Helpers
# ------------------------------------------------------------------

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
]
