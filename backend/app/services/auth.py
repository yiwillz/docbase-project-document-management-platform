"""登录（PRD F10.1）：认证提供方可插拔，第一版只有本地账号；会话放在数据库里，浏览器 cookie 里只有随机令牌。

- 密码：scrypt 加盐哈希（Python 自带，不加依赖）；最短 DOCBASE_PASSWORD_MIN_LENGTH 位（默认 8）；
- 会话：DOCBASE_SESSION_HOURS 小时（默认 12）没操作就过期；每次用到都往后顺延；退出、改密码、停用账号即作废；
- 防乱试：同一个账号连续输错 5 次锁 5 分钟；
- 以后接 AD / LDAP、单点登录（OIDC）：实现 AuthProvider，按 users.auth_provider 选；本地以外的账号不存密码。
"""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuthSession, User
from app.services.errors import Invalid, Unauthorized

COOKIE = "docbase_session"
MAX_FAILS, LOCK_MINUTES = 5, 5
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


# ---------------------------------------------------------------- 密码


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r),
                             p=int(p), dklen=len(base64.b64decode(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, base64.b64decode(digest))


def check_password_rules(password: str) -> None:
    n = get_settings().password_min_length
    if len(password or "") < n:
        raise Invalid("auth.password_too_short", f"密码至少 {n} 位", min_length=n)


# ---------------------------------------------------------------- 认证提供方


class AuthProvider(Protocol):
    name: str

    def authenticate(self, session: Session, username: str, password: str) -> User | None:
        """用户名密码对得上就返回用户，否则 None（锁定、停用由调用方处理）。"""


class LocalProvider:
    name = "local"

    def authenticate(self, session: Session, username: str, password: str) -> User | None:
        user = session.scalar(select(User).where(User.username == username, User.auth_provider == "local"))
        if user is None:
            verify_password(password, hash_password("timing"))  # 用户不存在时也花同样的时间
            return None
        return user if verify_password(password, user.password_hash) else None


PROVIDERS: dict[str, AuthProvider] = {"local": LocalProvider()}


# ---------------------------------------------------------------- 登录、会话


def _now() -> datetime:
    return datetime.now(UTC)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(session: Session, username: str, password: str, *, ip: str | None = None,
          user_agent: str | None = None) -> tuple[str, User]:
    """登录成功返回 (cookie 里的令牌, 用户)；用户名或密码不对、被锁、停用都给同样含糊的提示（锁定除外）。"""
    username = (username or "").strip()
    user = session.scalar(select(User).where(User.username == username))
    now = _now()
    if user is not None and user.locked_until and user.locked_until > now:
        minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        raise Unauthorized("auth.locked", f"密码输错太多次，请 {minutes} 分钟后再试", minutes=minutes)
    provider = PROVIDERS.get(user.auth_provider if user else "local", PROVIDERS["local"])
    ok = provider.authenticate(session, username, password or "")
    if ok is None or not ok.active:
        if user is not None:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= MAX_FAILS:
                user.locked_until, user.failed_logins = now + timedelta(minutes=LOCK_MINUTES), 0
            session.commit()
        raise Unauthorized("auth.bad_credentials", "用户名或密码不对")
    ok.failed_logins, ok.locked_until, ok.last_login_at = 0, None, now
    token = secrets.token_urlsafe(32)
    session.add(AuthSession(token_hash=_token_hash(token), user_id=ok.id, expires_at=now + _lifetime(),
                            ip=(ip or "")[:64] or None, user_agent=(user_agent or "")[:300] or None))
    session.commit()
    return token, ok


def _lifetime() -> timedelta:
    return timedelta(hours=get_settings().session_hours)


def resolve(session: Session, token: str | None) -> User | None:
    """cookie 里的令牌 → 用户；过期、作废、账号停用返回 None。用到就把过期时间往后顺延。"""
    if not token:
        return None
    s = session.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    now = _now()
    if s is None or s.revoked_at is not None or s.expires_at <= now:
        return None
    user = session.get(User, s.user_id)
    if user is None or not user.active:
        return None
    if (now - s.last_seen_at).total_seconds() > 60:  # 一分钟内只写一次，免得每个请求都写库
        s.last_seen_at, s.expires_at = now, now + _lifetime()
        session.commit()
    return user


def logout(session: Session, token: str | None) -> None:
    if token:
        session.execute(update(AuthSession).where(AuthSession.token_hash == _token_hash(token),
                                                  AuthSession.revoked_at.is_(None)).values(revoked_at=_now()))
        session.commit()


def revoke_all(session: Session, user_id: int, *, keep_token: str | None = None) -> None:
    """改密码、停用账号时：这个人别的会话全部作废（keep_token 是当前这个，保留）。"""
    stmt = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if keep_token:
        stmt = stmt.where(AuthSession.token_hash != _token_hash(keep_token))
    session.execute(stmt.values(revoked_at=_now()))


def change_password(session: Session, user: User, old: str, new: str, *, keep_token: str | None) -> None:
    if not verify_password(old or "", user.password_hash):
        raise Invalid("auth.bad_password", "原密码不对")
    check_password_rules(new)
    user.password_hash, user.password_changed_at = hash_password(new), _now()
    revoke_all(session, user.id, keep_token=keep_token)


# ---------------------------------------------------------------- 只给自动测试用


def dev_mode_allowed() -> bool:
    """X-User 请求头、匿名当管理员：只在连测试库（库名以 _test 结尾）时才允许。"""
    db = make_url(get_settings().database_url).database or ""
    return db.endswith("_test")


def dev_user(session: Session, username: str) -> User:
    """测试用：按用户名找（没有就建一个系统管理员）。"""
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username, display_name=username, is_sysadmin=True, auth_provider="local")
        session.add(user)
        session.flush()
    return user
