import hashlib
import hmac
import json
import os
import time
import io
from pathlib import Path
from typing import Optional, Dict, Tuple

import pyotp
import qrcode
import streamlit as st

from config.settings import USERS_FILE, APP_NAME


def _drive_upload(path):
    try:
        from utils.gdrive import upload
        upload(path)
    except Exception:
        pass


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return dk.hex()


def load_users() -> Dict:
    if not USERS_FILE.exists():
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_users(users: Dict):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


def has_users() -> bool:
    return bool(load_users())


def create_user(username: str, password: str,
                totp_enabled: bool = True, email: str = "") -> Tuple[bool, Optional[str]]:
    users = load_users()
    if username in users:
        return False, None
    salt          = os.urandom(32).hex()
    password_hash = _hash_password(password, salt)
    totp_secret   = pyotp.random_base32() if totp_enabled else None
    users[username] = {
        "password_hash": password_hash,
        "salt":          salt,
        "totp_secret":   totp_secret,
        "totp_enabled":  totp_enabled,
        "email":         email,
        "created_at":    time.time(),
    }
    save_users(users)
    _drive_upload(USERS_FILE)
    return True, totp_secret


def update_profile(username: str,
                   new_password: Optional[str] = None,
                   new_email: Optional[str] = None) -> bool:
    users = load_users()
    if username not in users:
        return False
    if new_password:
        salt = os.urandom(32).hex()
        users[username]["password_hash"] = _hash_password(new_password, salt)
        users[username]["salt"] = salt
    if new_email is not None:
        users[username]["email"] = new_email
    save_users(users)
    _drive_upload(USERS_FILE)
    return True


def get_profile(username: str) -> Dict:
    users = load_users()
    u = users.get(username, {})
    return {"email": u.get("email", ""), "totp_enabled": u.get("totp_enabled", False)}


def verify_password(username: str, password: str) -> bool:
    users = load_users()
    if username not in users:
        return False
    expected = _hash_password(password, users[username]["salt"])
    return hmac.compare_digest(expected, users[username]["password_hash"])


def is_totp_enabled(username: str) -> bool:
    return load_users().get(username, {}).get("totp_enabled", False)


def verify_totp(username: str, code: str) -> bool:
    users = load_users()
    if username not in users:
        return False
    user = users[username]
    if not user.get("totp_enabled"):
        return True
    return pyotp.TOTP(user["totp_secret"]).verify(code.strip(), valid_window=1)


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
    for key in ["authenticated", "username", "_gdrive_synced", "_users_synced"]:
        st.session_state.pop(key, None)
