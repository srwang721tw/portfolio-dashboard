import streamlit as st
from config.settings import APP_NAME, APP_ICON
from utils.auth import (
    has_users, create_user, verify_password, verify_totp,
    is_totp_enabled, get_totp_qr_bytes, logout,
)

st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar logout ──────────────────────────────────────────────────────────
if st.session_state.get("authenticated"):
    with st.sidebar:
        st.markdown(f"**👤 {st.session_state.username}**")
        if st.button("登出", use_container_width=True):
            logout()
            st.rerun()


# ── First-time setup ─────────────────────────────────────────────────────────
def show_setup():
    st.title("🔧 初始化設定")
    st.info("第一次使用，請建立管理員帳號。")

    with st.form("setup_form"):
        username = st.text_input("使用者名稱")
        password = st.text_input("密碼（至少 8 字元）", type="password")
        password2 = st.text_input("確認密碼", type="password")
        enable_totp = st.checkbox("啟用雙因子驗證 (Google Authenticator)", value=True)
        submitted = st.form_submit_button("建立帳號", use_container_width=True)

    if submitted:
        if not username or not password:
            st.error("帳號和密碼不能為空。")
            return
        if len(password) < 8:
            st.error("密碼至少需要 8 個字元。")
            return
        if password != password2:
            st.error("兩次密碼不一致。")
            return

        success, totp_secret = create_user(username, password, enable_totp)
        if not success:
            st.error("帳號已存在。")
            return

        st.success(f"帳號 **{username}** 建立成功！")

        if enable_totp and totp_secret:
            st.markdown("### 掃描 QR Code 設定 Google Authenticator")
            st.markdown("請用 **Google Authenticator** 或 **Authy** 掃描下方 QR Code：")
            qr_bytes = get_totp_qr_bytes(username, totp_secret)
            col1, col2 = st.columns([1, 2])
            with col1:
                st.image(qr_bytes, width=220)
            with col2:
                st.code(totp_secret, language=None)
                st.caption("或手動輸入上方金鑰")
            st.warning("⚠️ 請先完成 Authenticator 設定，再回到登入頁面。")
            st.info("設定完成後，請重新整理頁面並登入。")
        else:
            st.balloons()
            st.info("設定完成，請登入。")


# ── Login ────────────────────────────────────────────────────────────────────
def show_login():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown(f"## {APP_ICON} {APP_NAME}")
        st.markdown("---")

        with st.form("login_form"):
            username = st.text_input("帳號", placeholder="請輸入帳號")
            password = st.text_input("密碼", type="password", placeholder="請輸入密碼")
            totp_code = st.text_input(
                "驗證碼（2FA）",
                placeholder="Google Authenticator 6 位數字",
                max_chars=6,
            )
            submitted = st.form_submit_button("登入", use_container_width=True)

        if submitted:
            if not verify_password(username, password):
                st.error("帳號或密碼錯誤。")
                return

            if is_totp_enabled(username):
                if not totp_code:
                    st.warning("請輸入雙因子驗證碼。")
                    return
                if not verify_totp(username, totp_code):
                    st.error("驗證碼錯誤或已過期。")
                    return

            st.session_state.authenticated = True
            st.session_state.username = username
            st.rerun()


# ── Main router ───────────────────────────────────────────────────────────────
if not has_users():
    show_setup()
elif not st.session_state.get("authenticated"):
    show_login()
else:
    # Redirect to overview page after login
    st.switch_page("pages/1_Overview.py")
