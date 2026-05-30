import hashlib
import hmac
import io
import os
import time
from typing import Optional, Dict, Tuple

import pyotp
import qrcode
import streamlit as st

from config.settings import APP_NAME
import utils.db as db


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return dk.hex()


def has_users() -> bool:
    return bool(db.list_usernames())


def create_user(username: str, password: str,
                totp_enabled: bool = True, email: str = "") -> Tuple[bool, Optional[str]]:
    if db.get_user(username) is not None:
        return False, None
    salt          = os.urandom(32).hex()
    password_hash = _hash_password(password, salt)
    totp_secret   = pyotp.random_base32() if totp_enabled else None
    db.upsert_user(
        username=username,
        password_hash=password_hash,
        salt=salt,
        totp_secret=totp_secret,
        totp_enabled=totp_enabled,
        email=email,
        created_at=time.time(),
    )
    return True, totp_secret


def update_profile(username: str,
                   new_password: Optional[str] = None,
                   new_email: Optional[str] = None) -> bool:
    if db.get_user(username) is None:
        return False
    if new_password:
        salt          = os.urandom(32).hex()
        password_hash = _hash_password(new_password, salt)
        db.update_user_password(username, password_hash, salt)
    if new_email is not None:
        db.update_user_email(username, new_email)
    return True


def get_profile(username: str) -> Dict:
    u = db.get_user(username) or {}
    return {"email": u.get("email", ""), "totp_enabled": u.get("totp_enabled", False)}


def verify_password(username: str, password: str) -> bool:
    u = db.get_user(username)
    if u is None:
        return False
    expected = _hash_password(password, u["salt"])
    return hmac.compare_digest(expected, u["password_hash"])


def is_totp_enabled(username: str) -> bool:
    return (db.get_user(username) or {}).get("totp_enabled", False)


def verify_totp(username: str, code: str) -> bool:
    u = db.get_user(username)
    if u is None:
        return False
    if not u.get("totp_enabled"):
        return True
    return pyotp.TOTP(u["totp_secret"]).verify(code.strip(), valid_window=1)


def get_totp_qr_bytes(username: str, secret: str) -> io.BytesIO:
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=APP_NAME)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def require_auth():
    if not st.session_state.get("authenticated"):
        st.warning("請先登入。")
        st.stop()


def logout():
    for key in ["authenticated", "username"]:
        st.session_state.pop(key, None)
