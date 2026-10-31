"""Everything shown before and during sign-in: the landing page with its two portals, the two
sign-in screens, first-time setup, and the "choose a new password" screen.

Choosing a portal here is only a convenience. The server checks the account's real role in its
database and refuses a mismatch; nothing in this file decides what anyone may do.
"""

from typing import Any

import streamlit as st

import ui
from api_client import ApiClient, ApiError, ApiUnreachable

PORTALS = {
    "employee": {
        "icon": "\U0001f9d1‍\U0001f527",
        "title": "Employee Login",
        "blurb": (
            "Ask questions about your own documents and the company knowledge you are "
            "allowed to see."
        ),
        "button": "Continue as employee",
        "who": "employee",
    },
    "admin": {
        "icon": "\U0001f6e1️",
        "title": "Super Admin Login",
        "blurb": "Manage employee accounts, company documents and the audit log.",
        "button": "Continue as Super Admin",
        "who": "Super Admin",
    },
}

_CSS = """
<style>
.apex-hero { text-align: center; padding: 1.5rem 0 0.5rem; }
.apex-hero .apex-company { letter-spacing: 0.12em; font-size: 0.8rem; font-weight: 600;
  color: #1F4E79; text-transform: uppercase; }
.apex-hero h1 { margin: 0.2rem 0 0.2rem; font-size: 2rem; color: #1B2733; }
.apex-hero p { margin: 0; color: #5b6b7b; }
.apex-card-icon { font-size: 2.2rem; line-height: 1; }
.apex-card-title { font-size: 1.25rem; font-weight: 650; color: #1F4E79; margin: 0.4rem 0 0.2rem; }
.apex-card-blurb { color: #5b6b7b; min-height: 3.2rem; margin-bottom: 0.6rem; }
@media (max-width: 640px) {
  .apex-hero h1 { font-size: 1.5rem; }
  .apex-card-blurb { min-height: 0; }
}
</style>
"""


def _header() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="apex-hero"><div class="apex-company">'
        "Apex Precision Manufacturing Pvt. Ltd.</div>"
        "<h1>Knowledge Assistant</h1>"
        "<p>Answers from your company's own documents</p></div>",
        unsafe_allow_html=True,
    )


def _centered() -> Any:
    _, middle, _ = st.columns([1, 3, 1])
    return middle


# ----------------------------------------------------------------------------- portal choice


def selected_portal() -> str | None:
    """The portal picked on the landing page (or in a bookmarked link), if any."""
    portal = st.session_state.get("portal") or st.query_params.get("portal")
    return portal if portal in PORTALS else None


def choose_portal(portal: str | None) -> None:
    if portal in PORTALS:
        st.session_state["portal"] = portal
        st.query_params["portal"] = portal
    else:
        st.session_state.pop("portal", None)
        st.query_params.pop("portal", None)
    st.rerun()


def forget_portal() -> None:
    """After signing out: back to the landing page."""
    st.session_state.pop("portal", None)
    st.query_params.pop("portal", None)


def landing_page() -> None:
    _header()
    if notice := st.session_state.pop("notice", None):
        st.info(notice)
    with _centered():
        left, right = st.columns(2)
        for column, key in ((left, "employee"), (right, "admin")):
            portal = PORTALS[key]
            with column, st.container(border=True):
                st.markdown(
                    f'<div class="apex-card-icon">{portal["icon"]}</div>'
                    f'<div class="apex-card-title">{portal["title"]}</div>'
                    f'<div class="apex-card-blurb">{portal["blurb"]}</div>',
                    unsafe_allow_html=True,
                )
                if st.button(
                    portal["button"],
                    key=f"portal-{key}",
                    type="primary" if key == "employee" else "secondary",
                    use_container_width=True,
                ):
                    choose_portal(key)
        st.caption("Accounts are created by a Super Admin. There is no self sign-up.")


# ----------------------------------------------------------------------------- sign in


def sign_in_message(error: ApiError, portal: str) -> str:
    if error.status == 401 and error.detail == "invalid_credentials":
        who = PORTALS[portal]["who"]
        return (
            f"That Login ID and password do not match an active {who} account. "
            "Check them, or use the other sign-in option if this is not a "
            f"{who} account."
        )
    return ui.retry_message(error)


def sign_in_page(client: ApiClient, portal: str) -> None:
    _header()
    details = PORTALS[portal]
    with _centered():
        if notice := st.session_state.pop("notice", None):
            st.info(notice)
        with st.container(border=True):
            st.markdown(f"### {details['icon']} {details['title']}")
            with st.form("login"):
                login_id = st.text_input("Login ID", help="Your email address works too.")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button(
                    "Sign in", type="primary", use_container_width=True
                )
            if submitted:
                try:
                    client.login(login_id, password, portal=portal)
                except ApiError as error:
                    st.error(sign_in_message(error, portal), icon="\U0001f512")
                except ApiUnreachable:
                    st.error("Cannot reach the API. Is it running?", icon="\U0001f50c")
                else:
                    st.rerun()
        if st.button("← Back to the sign-in options", key="back"):
            choose_portal(None)
        st.caption("Accounts are created by a Super Admin. There is no self sign-up.")


# ----------------------------------------------------------------------------- first-time setup


def setup_status(client: ApiClient) -> dict[str, Any]:
    """Whether the system still needs its first account. If we can't tell, show the normal page."""
    try:
        return client.setup_status()
    except (ApiError, ApiUnreachable):
        return {"setup_required": False, "token_required": False, "available": False}


def setup_page(client: ApiClient, status: dict[str, Any]) -> None:
    """First run only: no account exists, so the first person creates the Super Admin here."""
    _header()
    with _centered(), st.container(border=True):
        st.subheader("First-time setup")
        if not status["available"]:
            st.warning(
                "No account exists yet, and setting one up from this page is switched off. "
                "Either set SETUP_TOKEN in .env and restart the API, or create the Super Admin "
                "on the command line:"
            )
            st.code(
                'python -m app.cli create-user --email you@company.com --name "Your Name" '
                "--role super_admin"
            )
            return
        st.write(
            "No accounts exist yet. Create the Super Admin account for this system. "
            "This page disappears as soon as it exists; other accounts are added later "
            "from the Employee Management page."
        )
        with st.form("setup"):
            full_name = st.text_input("Full name")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password", help="At least 12 characters.")
            repeat = st.text_input("Repeat password", type="password")
            token = (
                st.text_input("Setup token", type="password", help="The SETUP_TOKEN value in .env.")
                if status["token_required"]
                else ""
            )
            submitted = st.form_submit_button(
                "Create Super Admin account", type="primary", use_container_width=True
            )
        if not submitted:
            return
        if problem := ui.setup_problem(full_name, email, password, repeat):
            st.error(problem)
            return
        try:
            client.setup(email, full_name, password, token)
            client.login(email, password, portal="admin")
        except ApiError as error:
            st.error(ui.retry_message(error), icon="\U0001f512")
            return
        except ApiUnreachable:
            st.error("Cannot reach the API. Is it running?", icon="\U0001f50c")
            return
        choose_portal("admin")


# ----------------------------------------------------------------------------- the entry point


def signed_out_page(client: ApiClient) -> None:
    """Whatever a signed-out visitor should see right now."""
    status = setup_status(client)
    if status["setup_required"]:
        setup_page(client, status)
        return
    portal = selected_portal()
    if portal is None:
        landing_page()
    else:
        sign_in_page(client, portal)


def password_change_page(client: ApiClient) -> None:
    """Shown instead of the app while a temporary password is still in force."""
    _header()
    with _centered(), st.container(border=True):
        st.markdown("### Choose a new password")
        st.write(
            "You signed in with a temporary password. Choose your own to continue. "
            "It must be at least 12 characters."
        )
        with st.form("change-password"):
            current = st.text_input("Current (temporary) password", type="password")
            new = st.text_input("New password", type="password")
            repeat = st.text_input("Repeat new password", type="password")
            submitted = st.form_submit_button(
                "Change password", type="primary", use_container_width=True
            )
        if st.button("Sign out", use_container_width=True):
            client.logout()
            forget_portal()
            st.rerun()
        if not submitted:
            return
        if not current or not new:
            st.error("Please fill in all three boxes.")
            return
        if new != repeat:
            st.error("The two new passwords do not match.")
            return
        try:
            client.change_password(current, new)
        except ApiError as error:
            st.error(ui.retry_message(error), icon="\U0001f512")
            return
        except ApiUnreachable:
            st.error("Cannot reach the API. Is it running?", icon="\U0001f50c")
            return
        st.rerun()
