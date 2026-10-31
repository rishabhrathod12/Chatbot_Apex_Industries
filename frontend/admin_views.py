"""The Super Admin workspace: overview, people, departments and the audit log."""

from datetime import date, datetime, time
from typing import Any

import streamlit as st

import ui
from api_client import ApiClient, ApiError

PAGE_SIZE = 25


# ----------------------------------------------------------------------------- overview


def overview_page(client: ApiClient) -> None:
    st.title("Overview")
    try:
        stats = client.admin_stats()
    except ApiError as error:
        st.error(ui.error_message(error))
        return
    accounts, documents = stats["accounts"], stats["documents"]

    first = st.columns(4)
    first[0].metric("Accounts", accounts["total"])
    first[1].metric("Active", accounts["active"])
    first[2].metric("Disabled", accounts["inactive"])
    first[3].metric("Company documents", documents["total"])
    second = st.columns(4)
    roles = accounts["by_role"]
    second[0].metric("Super Admins", roles.get("super_admin", 0))
    second[1].metric("Department managers", roles.get("department_manager", 0))
    second[2].metric("Employees", roles.get("employee", 0))
    second[3].metric("Archived documents", documents["archived"])

    st.subheader("By department")
    st.dataframe(
        [
            {
                "Department": ui.department_label(d["code"]),
                "Employees": d["employees"],
                "Managers": d["managers"],
                "Company documents": d["documents"],
            }
            for d in stats["departments"]
        ],
        hide_index=True,
        use_container_width=True,
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Recent uploads")
        if not stats["recent_uploads"]:
            st.caption("No company documents yet.")
        for upload in stats["recent_uploads"]:
            st.markdown(f"**{ui.escape_markdown(upload['title'])}**")
            st.caption(
                f"{ui.department_label(upload['department'])} · {upload['status']} · "
                f"{ui.escape_markdown(upload['uploaded_by'])} · "
                f"{ui.short_time(upload['created_at'])}"
            )
    with right:
        st.subheader("Recent administrative activity")
        if not stats["recent_activity"]:
            st.caption("Nothing yet.")
        for entry in stats["recent_activity"]:
            st.markdown(ui.escape_markdown(ui.audit_summary(entry)))
            st.caption(ui.short_time(entry["created_at"]))


# ----------------------------------------------------------------------------- people


def show_temporary_password(password: str) -> None:
    st.warning(
        "Give the person this temporary password now. It is shown only once and cannot be "
        "looked up later (you can reset it again if it is lost).",
        icon="\U0001f511",
    )
    st.code(password, language=None)


def show_new_account(created: dict[str, Any]) -> None:
    st.success(
        f"Created {created['email']}. Login ID: {created.get('login_id', '')}. "
        "They must choose their own password at first sign-in."
    )
    if created.get("temporary_password"):
        show_temporary_password(created["temporary_password"])


def create_employee_page(client: ApiClient) -> None:
    st.title("Create employee")
    st.caption(
        "The person signs in with the Login ID and the temporary password, then chooses their own."
    )
    with st.form("create-user", clear_on_submit=True):
        left, right = st.columns(2)
        full_name = left.text_input("Full name")
        email = right.text_input("Email")
        role_label = left.selectbox("Role", list(ui.ROLES.values()), index=2)
        names = right.multiselect("Departments", list(ui.DEPARTMENTS.values()))
        login_id = left.text_input(
            "Login ID (optional)", help="Leave empty to make one from the name."
        )
        password = right.text_input(
            "Temporary password (optional)",
            type="password",
            help="Leave empty to generate a secure one. They must change it at first sign-in.",
        )
        submitted = st.form_submit_button("Create account", type="primary")
    if not submitted:
        return
    role = next(code for code, label in ui.ROLES.items() if label == role_label)
    departments = [c for c, n in ui.DEPARTMENTS.items() if n in names]
    try:
        created = client.create_user(
            email, full_name, password or None, role, departments, login_id.strip() or None
        )
    except ApiError as error:
        st.error(ui.error_message(error))
        return
    show_new_account(created)


def _edit_popover(client: ApiClient, person: dict[str, Any]) -> None:
    key = person["id"]
    with st.popover("Edit"):
        role_label = st.selectbox(
            "Role",
            list(ui.ROLES.values()),
            index=list(ui.ROLES).index(person["role"]),
            key=f"role-{key}",
        )
        current = [ui.DEPARTMENTS[d] for d in person["departments"] if d in ui.DEPARTMENTS]
        names = st.multiselect(
            "Departments", list(ui.DEPARTMENTS.values()), default=current, key=f"deps-{key}"
        )
        if st.button("Save changes", key=f"save-{key}"):
            role = next(code for code, label in ui.ROLES.items() if label == role_label)
            departments = [c for c, n in ui.DEPARTMENTS.items() if n in names]
            try:
                client.update_user(key, role=role, departments=departments)
            except ApiError as error:
                st.error(ui.error_message(error))
                return
            st.rerun()


def _reset_popover(client: ApiClient, person: dict[str, Any]) -> None:
    key = person["id"]
    with st.popover("Reset password"):
        new_password = st.text_input(
            "New temporary password (optional)",
            type="password",
            help="Leave empty to generate one.",
            key=f"pw-{key}",
        )
        if st.button("Reset password", key=f"setpw-{key}"):
            try:
                temporary = client.reset_password(key, new_password or None)
            except ApiError as error:
                st.error(ui.error_message(error))
                return
            st.success(
                "Password reset. Their old sessions have ended and they must choose a new "
                "password at next sign-in."
            )
            if temporary:
                show_temporary_password(temporary)


def user_row(client: ApiClient, person: dict[str, Any], me: str) -> None:
    with st.container(border=True):
        info, status, actions = st.columns([5, 2, 4])
        with info:
            st.markdown(f"**{ui.escape_markdown(person['full_name'])}**")
            places = ", ".join(ui.department_label(d) for d in person["departments"]) or "All"
            st.caption(
                f"Login ID: {person['login_id']} · {person['email']} · "
                f"{ui.role_label(person['role'])} · {places}"
            )
        with status:
            active = person["is_active"]
            st.badge("Active" if active else "Disabled", color="green" if active else "gray")
            if person.get("must_change_password"):
                st.badge("Must change password", color="orange")
        with actions:
            if person["id"] != me:
                action = "Disable" if person["is_active"] else "Enable"
                if st.button(action, key=f"toggle-{person['id']}"):
                    try:
                        client.update_user(person["id"], is_active=not person["is_active"])
                    except ApiError as error:
                        st.error(ui.error_message(error))
                        return
                    st.rerun()
                _edit_popover(client, person)
            _reset_popover(client, person)


def employees_page(client: ApiClient, me: str) -> None:
    st.title("Employee management")
    st.caption("Changes apply immediately, even to people who are signed in.")
    try:
        people = client.list_users()
    except ApiError as error:
        st.error(ui.error_message(error))
        return

    first, second, third, fourth = st.columns(4)
    text = first.text_input("Search name, Login ID or email", key="people-search").strip().lower()
    role = second.selectbox("Role", ["All", *ui.ROLES.values()], key="people-role")
    status = third.selectbox("Status", ["All", "Active", "Disabled"], key="people-status")
    department = fourth.selectbox(
        "Department", [ui.ALL_DEPARTMENTS, *ui.DEPARTMENTS.values()], key="people-department"
    )

    shown = []
    for person in people:
        haystack = f"{person['full_name']} {person['login_id']} {person['email']}".lower()
        if text and text not in haystack:
            continue
        if role != "All" and ui.role_label(person["role"]) != role:
            continue
        if status != "All" and person["is_active"] != (status == "Active"):
            continue
        if department != ui.ALL_DEPARTMENTS and department not in [
            ui.department_label(d) for d in person["departments"]
        ]:
            continue
        shown.append(person)

    st.subheader(f"People ({len(shown)} of {len(people)})")
    for person in shown:
        user_row(client, person, me)


# ----------------------------------------------------------------------------- departments


def departments_page(client: ApiClient) -> None:
    st.title("Departments")
    st.caption(
        "The six departments are fixed. People and documents are assigned to them; "
        "change a person's departments under Employee Management."
    )
    try:
        stats = client.admin_stats()
        people = client.list_users()
    except ApiError as error:
        st.error(ui.error_message(error))
        return
    for department in stats["departments"]:
        code = department["code"]
        managers = [
            p["full_name"]
            for p in people
            if p["is_active"] and p["role"] == "department_manager" and code in p["departments"]
        ]
        with st.container(border=True):
            st.markdown(f"**{ui.department_label(code)}**")
            columns = st.columns(3)
            columns[0].metric("Employees", department["employees"])
            columns[1].metric("Managers", department["managers"])
            columns[2].metric("Company documents", department["documents"])
            st.caption(
                "Managers: " + (", ".join(ui.escape_markdown(m) for m in managers) or "none yet")
            )


# ----------------------------------------------------------------------------- audit log


def _start_of(day: date | None) -> str:
    return datetime.combine(day, time.min).isoformat() if day else ""


def _end_of(day: date | None) -> str:
    return datetime.combine(day, time.max).isoformat() if day else ""


def audit_page(client: ApiClient) -> None:
    st.title("Audit logs")
    st.caption(
        "Who did what, and whether it was allowed. Passwords and document text are never recorded."
    )
    try:
        people = {p["id"]: p for p in client.list_users()}
        actions = client.admin_audit(page=1, page_size=1)["available_actions"]
    except ApiError as error:
        st.error(ui.error_message(error))
        return

    first, second, third = st.columns(3)
    action = first.selectbox(
        "Action",
        ["All", *actions],
        format_func=lambda a: a if a == "All" else ui.action_label(a),
        key="audit-action",
    )
    outcome = second.selectbox(
        "Outcome",
        ["All", *ui.OUTCOME_LABELS],
        format_func=lambda o: ui.OUTCOME_LABELS.get(o, o),
        key="audit-outcome",
    )
    person = third.selectbox(
        "Person",
        ["", *people],
        format_func=lambda uid: "Everyone" if not uid else people[uid]["full_name"],
        key="audit-person",
    )
    fourth, fifth = st.columns(2)
    since = fourth.date_input("From", value=None, key="audit-from")
    until = fifth.date_input("To", value=None, key="audit-to")
    filters = {
        "action": "" if action == "All" else action,
        "outcome": "" if outcome == "All" else outcome,
        "user_id": person,
        "date_from": _start_of(since if isinstance(since, date) else None),
        "date_to": _end_of(until if isinstance(until, date) else None),
    }

    page = int(st.session_state.get("audit-page", 1))
    try:
        result = client.admin_audit(page=page, page_size=PAGE_SIZE, **filters)
    except ApiError as error:
        st.error(ui.error_message(error))
        return
    pages = max(1, -(-result["total"] // PAGE_SIZE))
    if page > pages:
        st.session_state["audit-page"] = page = pages

    st.subheader(f"{result['total']} entries")
    if result["items"]:
        st.dataframe(
            [
                {
                    "When (UTC)": ui.short_time(e["created_at"]),
                    "Who": e["actor_name"] or "(not signed in)",
                    "Action": ui.action_label(e["action"]),
                    "Outcome": ui.OUTCOME_LABELS.get(e["outcome"], e["outcome"]),
                    "About": e["target_label"] or "",
                    "Detail": e["detail"] or "",
                }
                for e in result["items"]
            ],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.caption("No entries match.")

    back, middle, forward = st.columns([1, 2, 1])
    if back.button("← Newer", disabled=page <= 1, key="audit-newer"):
        st.session_state["audit-page"] = page - 1
        st.rerun()
    middle.caption(f"Page {page} of {pages}")
    if forward.button("Older →", disabled=page >= pages, key="audit-older"):
        st.session_state["audit-page"] = page + 1
        st.rerun()
