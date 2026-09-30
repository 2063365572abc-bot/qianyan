import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlparse
from fastapi import Request, Response, HTTPException
from sqlalchemy import select
from .db import AccessSession, Space, now
from .config import config


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def verify_password(password, stored):
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(iterations) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def cookie_key(role):
    return "qianyan_owner" if role == "owner" else "qianyan_demo"


def create_session(db, space, response):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires = now() + timedelta(days=7 if space.role == "owner" else 1)
    db.add(AccessSession(id=hashlib.sha256(token.encode()).hexdigest(), space_id=space.id, csrf_token=csrf, expires_at=expires))
    response.set_cookie(cookie_key(space.role), token, httponly=True, secure=config().production, samesite="strict", max_age=int((expires - now()).total_seconds()))
    return {"role": space.role, "space_id": space.id, "csrf_token": csrf}


def valid_origin(request):
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {config().app_public_url.rstrip("/")}
    if not config().production:
        allowed.update({"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"})
    if origin.rstrip("/") not in allowed:
        raise HTTPException(403, "来源不允许 / Origin not allowed")


def authenticate(request, db, mutation=True):
    # Demo context is explicit so an owner's browser can explore without exposing
    # its owner space to anonymous demo features.
    prefer_demo = request.headers.get("x-qianyan-mode") == "demo"
    roles = ("demo",) if prefer_demo else ("owner", "demo")
    for role in roles:
        token = request.cookies.get(cookie_key(role))
        if not token:
            continue
        sess = db.get(AccessSession, hashlib.sha256(token.encode()).hexdigest())
        if not sess or sess.expires_at <= now():
            continue
        space = db.get(Space, sess.space_id)
        if not space or space.role != role or (space.expires_at and space.expires_at <= now()):
            continue
        if mutation and request.method not in ("GET", "HEAD", "OPTIONS"):
            valid_origin(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), sess.csrf_token):
                raise HTTPException(403, "会话校验失败，请刷新 / CSRF verification failed")
        return space, sess
    raise HTTPException(401, "请登录或进入样例空间 / Sign in or open demo")
