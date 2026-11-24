from dotenv import load_dotenv
from pathlib import Path

import streamlit as st
from pymongo.errors import PyMongoError

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

st.set_page_config(
    page_title="Faculty Leave Management",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import authenticate, reload_user, role_label
from constants import DEMO_PASSWORD
from database import DatabaseConfigError, close_client, get_db
import views

PAGES = {
    "Dashboard": views.dashboard,
    "Apply for leave": views.apply_page,
    "My requests": views.my_requests_page,
    "Leave balance": views.balance_page,
    "Holidays": views.holidays_page,
    "Approvals": views.approvals_page,
    "Department history": views.history_page,
    "Faculty history": views.history_page,
    "Reports": views.reports_page,
    "Users": views.users_page,
    "Leave types": views.leave_types_page,
    "Change Password": views.change_password_page,
}


def inject_css() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #d5e2ef;
            border-radius: 10px;
            padding: 0.7rem 0.85rem;
        }
        [data-testid="stSidebar"] { background: #e7eef6; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def navigation(role: str) -> list[str]:
    if role == "admin":
        return ["Dashboard", "Users", "Leave types", "Holidays", "Reports", "Change Password"]
    if role == "principal":
        return [
            "Dashboard",
            "Approvals",
            "Faculty history",
            "Reports",
            "Apply for leave",
            "My requests",
            "Leave balance",
            "Holidays",
            "Change Password",
        ]
    if role == "hod":
        return [
            "Dashboard",
            "Approvals",
            "Department history",
            "Apply for leave",
            "My requests",
            "Leave balance",
            "Holidays",
            "Change Password",
        ]
    return ["Dashboard", "Apply for leave", "My requests", "Leave balance", "Holidays", "Change Password"]


def connection_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "authentication failed" in text or "bad auth" in text:
        return "Atlas rejected the username or password in MONGODB_URI."
    if "timed out" in text or "serverselection" in text:
        return (
            "The app could not reach the Atlas cluster. In Network Access, allow your current IP, "
            "and confirm the cluster is running."
        )
    return "The app could not use MongoDB Atlas. Check MONGODB_URI and DATABASE_NAME."


def setup_screen(message: str) -> None:
    st.title("Faculty Leave Management System")
    st.error(message)
    st.subheader("Connect MongoDB Atlas")
    st.markdown(
        """
1. In Atlas, create a free cluster and a database user.
2. Under Network Access, allow your current IP.
3. Choose Connect → Drivers → Python and copy the connection string.
4. Copy `.env.example` to `.env` in this folder and paste the string as `MONGODB_URI`.
5. If the password contains `@`, `:`, `/`, `?`, `#`, or spaces, URL-encode those characters.
6. Save the file and use **Try again**.
        """
    )
    st.caption("The first successful connection creates demo accounts when the database has no users.")
    if st.button("Try again", type="primary"):
        close_client()
        load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
        st.rerun()


def login_screen(db) -> None:
    st.title("Faculty Leave Management System")
    st.write("Apply for leave, approve requests, and keep balances in one place.")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        if not username.strip() or not password:
            st.error("Enter both username and password.")
        else:
            try:
                user = authenticate(db, username, password)
            except PyMongoError:
                st.error("The database could not be reached. Check Atlas and try again.")
                return
            if user is None:
                st.error("Incorrect username or password, or the account is inactive.")
            else:
                st.session_state.user = user
                st.session_state.nav = "Dashboard"
                st.rerun()

    with st.expander("Demo accounts"):
        st.write(
            "These accounts are created when the database has no users. "
            f"The shared password is `{DEMO_PASSWORD}` until an admin changes it."
        )
        st.markdown(
            """
| Username | Role | Department |
| --- | --- | --- |
| admin | Admin | Administration |
| principal | Principal | Office of the Principal |
| sunita.rao | Head of Department | Computer Science |
| amit.verma | Head of Department | Electronics |
| anita.sharma | Faculty | Computer Science |
| rahul.mehta | Faculty | Computer Science |
| priya.nair | Faculty | Electronics |
            """
        )


def signed_in(db, user: dict) -> None:
    pages = navigation(user["role"])
    if st.session_state.get("nav") not in pages:
        st.session_state.nav = pages[0]
    st.sidebar.radio("Menu", pages, key="nav")
    st.sidebar.write(user["name"])
    st.sidebar.caption(f"{role_label(user['role'])} · {user['department']}")
    if st.sidebar.button("Log out", width="stretch"):
        st.session_state.clear()
        st.rerun()
    try:
        PAGES[st.session_state.nav](db, user)
    except PyMongoError:
        st.error("A database error interrupted this page. Check Atlas and try again.")


def main() -> None:
    inject_css()
    try:
        with st.spinner("Connecting to MongoDB Atlas..."):
            db = get_db()
    except DatabaseConfigError as exc:
        setup_screen(str(exc))
        return
    except PyMongoError as exc:
        setup_screen(connection_message(exc))
        return

    user = st.session_state.get("user")
    if not user:
        login_screen(db)
        return
    try:
        current = reload_user(db, user["id"])
    except PyMongoError:
        st.error("The database could not be reached. Check Atlas and try again.")
        return
    if current is None:
        st.session_state.clear()
        st.warning("This account is inactive. Sign in with another account.")
        login_screen(db)
        return
    st.session_state.user = current
    signed_in(db, current)


if __name__ == "__main__":
    main()
