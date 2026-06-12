import hashlib
import hmac
import logging
import os
import time
from typing import Optional, Dict, Tuple

import streamlit as st

import utils.db as db

_log = logging.getLogger(__name__)


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return dk.hex()


def has_users() -> bool:
    return bool(db.list_usernames())


def create_user(username: str, password: str, email: str = "") -> Tuple[bool, None]:
    if db.get_user(username) is not None:
        return False, None
    salt          = os.urandom(32).hex()
    password_hash = _hash_password(password, salt)
    db.upsert_user(
        username=username,
        password_hash=password_hash,
        salt=salt,
        totp_secret=None,
        totp_enabled=False,
        email=email,
        created_at=time.time(),
    )
    _log.info("Account created: username=%s", username)
    return True, None


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
    _log.info("Profile updated: username=%s", username)
    return True


def get_profile(username: str) -> Dict:
    u = db.get_user(username) or {}
    return {"email": u.get("email", "")}


def verify_password(username: str, password: str) -> bool:
    u = db.get_user(username)
    if u is None:
        _log.warning("Failed login — unknown username: %s", username)
        return False
    expected = _hash_password(password, u["salt"])
    result   = hmac.compare_digest(expected, u["password_hash"])
    if result:
        _log.info("Login: username=%s", username)
    else:
        _log.warning("Failed login — wrong password: username=%s", username)
    return result


def require_auth():
    if not st.session_state.get("authenticated"):
        st.warning("請先登入。")
        st.stop()


def logout():
    for key in ["authenticated", "username", "_last_activity"]:
        st.session_state.pop(key, None)
