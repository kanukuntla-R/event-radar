"""Authentication, authorization, and CSRF helpers for the Flask app
(V0.10C). The session holds only session["user_id"] - never a full user
record or password hash. g.user is resolved fresh from the DB on every
request via load_current_user() (an app.before_request hook); if the
stored user_id no longer exists, the stale session is cleared instead of
trusted.
"""
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import session, g, redirect, url_for, request, abort

from db import get_db
from user_store import get_user_by_id


def load_current_user():
    """app.before_request hook. Sets g.user to the authenticated user's
    row, or None. Never uses the bootstrap owner as a fallback for an
    unauthenticated request."""
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    conn = get_db()
    user = get_user_by_id(conn, user_id)
    conn.close()
    if user is None:
        session.clear()
        return
    g.user = user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Server-side enforcement of ADMIN ONLY routes - never rely on the
    template hiding a button. Unauthenticated => login redirect;
    authenticated non-admin => 403 (never silently no-ops)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        if not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def generate_csrf_token():
    """Lazily creates and returns this session's CSRF token. Safe to call
    for an anonymous (pre-login) session too - Flask's session cookie
    exists regardless of authentication state."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def validate_csrf_token(submitted):
    expected = session.get("csrf_token")
    return bool(expected) and bool(submitted) and secrets.compare_digest(expected, submitted)


def enforce_csrf():
    """app.before_request hook: every POST request must carry the
    session's current CSRF token in a `csrf_token` form field, including
    the login POST itself. Missing/invalid => reject with 400 before any
    view function (and therefore any mutation) runs."""
    if request.method == "POST":
        if not validate_csrf_token(request.form.get("csrf_token")):
            abort(400, description="Invalid or missing CSRF token.")


def is_safe_next_url(target):
    """Only a same-origin relative local path is a safe post-login
    redirect target - guards against an open-redirect via a crafted
    `next` value (an absolute URL, a protocol-relative //host URL, or a
    non-http(s) scheme like javascript:)."""
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.netloc and not parsed.scheme and target.startswith("/")
