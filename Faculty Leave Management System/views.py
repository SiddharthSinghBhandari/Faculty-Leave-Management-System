"""Screens shown after a user signs in."""

from datetime import date

import pandas as pd
import streamlit as st

from auth import role_label
from constants import (
    APPROVER_ROLES,
    LEAVE_ROLES,
    MAX_QUOTA,
    MAX_REASON_LENGTH,
    MIN_PASSWORD_LENGTH,
    REQUEST_STATUSES,
    ROLES,
)
from leave_service import (
    Result,
    add_holiday,
    admin_counts,
    apply_leave,
    balances_for_user,
    cancel_request,
    change_own_password,
    create_leave_type,
    create_user,
    decide_request,
    delete_holiday,
    department_balances,
    department_names,
    list_holidays,
    list_leave_types,
    list_users,
    my_requests,
    pending_requests,
    preview_leave,
    reset_password,
    set_leave_type_active,
    set_user_active,
    update_quota,
    visible_requests,
)
from reports import balances_frame, month_wise_frame, requests_frame, to_csv_bytes, to_pdf_bytes
from utils import format_date, today

PDF_REQUEST_COLUMNS = ["Faculty", "Department", "Leave type", "From", "To", "Days", "Status"]


def flash(message: str, kind: str = "success") -> None:
    st.session_state["flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("flash", None)
    if not item:
        return
    kind, message = item
    getattr(st, kind)(message)


def finish(result: Result) -> None:
    flash(result.message, "success" if result.ok else "error")
    st.rerun()


def open_page(name: str) -> None:
    st.session_state.nav = name


def require(user: dict, roles: tuple[str, ...]) -> None:
    if user["role"] not in roles:
        st.error("You do not have access to this page.")
        st.stop()


def page_header(title: str, subtitle: str) -> None:
    st.header(title)
    st.caption(subtitle)


def show_table(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("No records for this filter.")
        return
    st.dataframe(frame, width="stretch", hide_index=True)


def year_picker(key: str) -> int:
    current = today().year
    return int(st.selectbox("Year", [current, current - 1, current - 2], key=key))


def request_choices(rows: list[dict]) -> dict[str, dict]:
    choices = {}
    for row in rows:
        label = (
            f"{row['applicant']} · {format_date(row['start_date'])} to {format_date(row['end_date'])}"
            f" · {row['leave_type']} · {row['days']} day(s) · {row['id'][-4:]}"
        )
        choices[label] = row
    return choices


def show_request(row: dict) -> None:
    left, right = st.columns(2)
    left.write(row["applicant"])
    left.caption(row["department"])
    right.write(row["status"])
    right.caption(row["leave_type"])
    st.write(
        f"{format_date(row['start_date'])} to {format_date(row['end_date'])} · {row['days']} day(s)"
    )
    st.write(row["reason"])
    if row["remark"]:
        st.caption(f"Remark: {row['remark']}")
    if row["approver"]:
        st.caption(f"Decided by {row['approver']}")


def downloads(frame: pd.DataFrame, stem: str, title: str, pdf_columns: list[str] | None = None) -> None:
    pdf_frame = frame if pdf_columns is None else frame.loc[:, [column for column in pdf_columns if column in frame.columns]]
    csv_col, pdf_col = st.columns(2)
    csv_col.download_button(
        "Download CSV",
        data=to_csv_bytes(frame),
        file_name=f"{stem}.csv",
        mime="text/csv",
        key=f"{stem}-csv",
    )
    pdf_col.download_button(
        "Download PDF",
        data=to_pdf_bytes(pdf_frame, title),
        file_name=f"{stem}.pdf",
        mime="application/pdf",
        key=f"{stem}-pdf",
    )


def dashboard(db, user: dict) -> None:
    show_flash()
    page_header("Dashboard", f"{role_label(user['role'])} · {user['department']}")
    if user["role"] == "admin":
        counts = admin_counts(db)
        first, second, third, fourth = st.columns(4)
        first.metric("Active users", counts["users"])
        second.metric("Pending requests", counts["pending"])
        third.metric("Leave types", counts["leave_types"])
        fourth.metric(f"Holidays in {today().year}", counts["holidays"])
        st.caption("Quotas, holidays, and downloadable reports are in the admin menu.")
        return

    if user["role"] in APPROVER_ROLES:
        pending = pending_requests(db, user)
        waiting, _spacer = st.columns([1, 2])
        waiting.metric("Waiting for your decision", len(pending))
        if pending:
            st.button("Open the approval queue", type="primary", on_click=open_page, args=("Approvals",))
        else:
            scope = "your department" if user["role"] == "hod" else "the college"
            st.info(f"There are no pending requests for {scope}.")

    _own_snapshot(db, user)


def _own_snapshot(db, user: dict) -> None:
    year = today().year
    balances = balances_for_user(db, user, year)
    requests = my_requests(db, user)
    pending = sum(1 for row in requests if row["status"] == "Pending")
    approved_days = sum(row["days"] for row in requests if row["status"] == "Approved" and row["year"] == year)
    remaining = sum(row["remaining"] for row in balances)
    first, second, third = st.columns(3)
    first.metric("My pending requests", pending)
    second.metric(f"Approved days in {year}", approved_days)
    third.metric("Remaining days", remaining)
    if balances:
        chart = pd.DataFrame(
            {"Remaining": [row["remaining"] for row in balances]},
            index=[row["leave_type"] for row in balances],
        )
        st.bar_chart(chart)
        st.caption("Remaining = yearly quota − approved days. Dates on the holiday list are not counted.")
    st.subheader("Recent requests")
    frame = requests_frame(requests[:5]).drop(columns=["Faculty", "Department"])
    show_table(frame)


def apply_page(db, user: dict) -> None:
    require(user, LEAVE_ROLES)
    show_flash()
    page_header("Apply for leave", "The request stays Pending until the HoD or Principal decides it.")
    leave_types = list_leave_types(db, active_only=True)
    if not leave_types:
        st.warning("No leave types are available yet. Ask the admin to add one.")
        return

    labels = {item["name"]: item["id"] for item in leave_types}
    selected = st.selectbox("Leave type", list(labels))
    start_col, end_col = st.columns(2)
    start = start_col.date_input("From", value=today(), key="apply-start")
    end = end_col.date_input("To", value=today(), key="apply-end")
    preview = preview_leave(db, user, labels[selected], start, end)
    if preview.ok:
        st.info(preview.message)
    else:
        st.warning(preview.message)
    reason = st.text_area("Reason", max_chars=MAX_REASON_LENGTH, placeholder="Why do you need this leave?")
    if st.button("Submit application", type="primary", disabled=not preview.ok):
        finish(apply_leave(db, user, labels[selected], start, end, reason))


def my_requests_page(db, user: dict) -> None:
    require(user, LEAVE_ROLES)
    show_flash()
    page_header("My requests", "Cancel a request while it is still Pending. Approved days stay on the balance.")
    status = st.selectbox("Status", ["All", *REQUEST_STATUSES], key="mine-status")
    rows = my_requests(db, user)
    if status != "All":
        rows = [row for row in rows if row["status"] == status]
    show_table(requests_frame(rows).drop(columns=["Faculty", "Department"]))

    pending = [row for row in my_requests(db, user) if row["status"] == "Pending"]
    if not pending:
        return
    st.subheader("Cancel a pending request")
    choices = request_choices(pending)
    selected = choices[st.selectbox("Pending request", list(choices), key="cancel-choice")]
    show_request(selected)
    if st.button("Cancel this request"):
        finish(cancel_request(db, user, selected["id"]))


def balance_page(db, user: dict) -> None:
    require(user, LEAVE_ROLES)
    show_flash()
    page_header("Leave balance", "Used days increase only when a request is approved.")
    year = year_picker("balance-year")
    rows = balances_for_user(db, user, year)
    frame = balances_frame(rows).drop(columns=["Faculty", "Department"])
    show_table(frame)
    overdrawn = [row for row in rows if row["remaining"] < 0]
    if overdrawn:
        st.warning("Used days are above the current quota for at least one leave type. New requests for that type will be blocked.")


def holidays_page(db, user: dict) -> None:
    show_flash()
    page_header("Holiday list", "A holiday date inside a request is not counted as leave.")
    year = year_picker("holiday-year")
    rows = list_holidays(db, year)
    if rows:
        frame = pd.DataFrame(
            {"Date": [format_date(row["date"]) for row in rows], "Description": [row["description"] for row in rows]}
        )
        show_table(frame)
    else:
        st.info(f"No holidays are recorded for {year}.")

    if user["role"] != "admin":
        st.caption("Weekly offs count as leave unless they are added here.")
        return

    st.subheader("Add a holiday")
    with st.form("add-holiday"):
        holiday = st.date_input("Date", value=date(year, 1, 1), key="new-holiday")
        description = st.text_input("Description", placeholder="Republic Day")
        submitted = st.form_submit_button("Add holiday", type="primary")
    if submitted:
        finish(add_holiday(db, user, holiday, description))

    if not rows:
        return
    st.subheader("Remove a holiday")
    labels = {f"{format_date(row['date'])} · {row['description']}": row["id"] for row in rows}
    choice = st.selectbox("Holiday", list(labels), key="delete-holiday")
    if st.button("Remove holiday"):
        finish(delete_holiday(db, user, labels[choice]))


def approvals_page(db, user: dict) -> None:
    require(user, APPROVER_ROLES)
    show_flash()
    scope = "faculty in your department" if user["role"] == "hod" else "any request except your own"
    page_header("Approvals", f"You can decide {scope}. Approval deducts the leave balance.")
    rows = pending_requests(db, user)
    if not rows:
        st.info("The approval queue is empty.")
        return
    choices = request_choices(rows)
    selected = choices[st.selectbox("Pending request", list(choices), key="approval-choice")]
    show_request(selected)
    remark = st.text_area("Remark", key=f"remark-{selected['id']}", placeholder="Required when you reject.")
    approve_col, reject_col = st.columns(2)
    if approve_col.button("Approve", type="primary"):
        finish(decide_request(db, user, selected["id"], True, remark))
    if reject_col.button("Reject"):
        finish(decide_request(db, user, selected["id"], False, remark))


def history_page(db, user: dict) -> None:
    require(user, APPROVER_ROLES)
    show_flash()
    if user["role"] == "hod":
        page_header("Department history", user["department"])
        department = None
    else:
        page_header("Faculty history", "Every department, or one department at a time.")
        names = department_names(db)
        picked = st.selectbox("Department", ["All", *names], key="history-department")
        department = None if picked == "All" else picked
    year_col, status_col = st.columns(2)
    with year_col:
        year = year_picker("history-year")
    with status_col:
        status_label = st.selectbox("Status", ["All", *REQUEST_STATUSES], key="history-status")
    status = None if status_label == "All" else status_label
    rows = visible_requests(db, user, year, department, status)
    show_table(requests_frame(rows))
    st.subheader(f"Balances in {year}")
    show_table(balances_frame(department_balances(db, user, year, department)))


def reports_page(db, user: dict) -> None:
    require(user, ("admin", "principal"))
    show_flash()
    page_header("Reports", "Faculty-wise balances and month-wise approved leave. CSV keeps the full text.")
    filter_col, department_col = st.columns(2)
    with filter_col:
        year = year_picker("report-year")
    with department_col:
        picked = st.selectbox("Department", ["All", *department_names(db)], key="report-department")
    department = None if picked == "All" else picked
    requests = visible_requests(db, user, year, department, None)
    balances = department_balances(db, user, year, department)

    st.subheader("Faculty-wise balance")
    balance_table = balances_frame(balances)
    show_table(balance_table)
    downloads(balance_table, f"faculty-balance-{year}", f"Faculty-wise leave balance {year}")

    st.subheader("Month-wise approved leave")
    month_table = month_wise_frame(requests)
    if not month_table.empty:
        chart = month_table.groupby("Month")["Days"].sum().to_frame()
        st.bar_chart(chart)
    show_table(month_table)
    downloads(month_table, f"month-wise-{year}", f"Month-wise approved leave {year}")

    st.subheader("Request register")
    status_label = st.selectbox("Request status", ["All", *REQUEST_STATUSES], key="report-status")
    status = None if status_label == "All" else status_label
    register = requests_frame(visible_requests(db, user, year, department, status))
    show_table(register)
    downloads(register, f"leave-register-{year}", f"Leave request register {year}", PDF_REQUEST_COLUMNS)


def change_password_page(db, user: dict) -> None:
    show_flash()
    page_header("Change Password", "Update your account password. Your current password is required for verification.")

    with st.form("change-own-password"):
        current_password = st.text_input("Current password", type="password")
        new_password = st.text_input(
            "New password",
            type="password",
            help=f"Use at least {MIN_PASSWORD_LENGTH} characters.",
        )
        confirm_password = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Change password", type="primary")

    if submitted:
        if not current_password or not new_password or not confirm_password:
            st.error("Fill in all three password fields.")
        elif new_password != confirm_password:
            st.error("The new password and confirmation do not match.")
        else:
            finish(change_own_password(db, user, current_password, new_password))


def users_page(db, user: dict) -> None:
    require(user, ("admin",))
    show_flash()
    page_header("Users", "New faculty, HoD, and Principal accounts receive this year's leave balance.")
    with st.form("create-user"):
        name = st.text_input("Full name")
        username = st.text_input("Username")
        email = st.text_input("Email")
        password = st.text_input("Temporary password", type="password")
        role = st.selectbox("Role", ROLES, format_func=role_label)
        department = st.selectbox("Department", department_names(db))
        submitted = st.form_submit_button("Add user", type="primary")
    if submitted:
        finish(create_user(db, user, name, username, email, password, role, department))

    people = list_users(db)
    display = pd.DataFrame(
        [
            {
                "Name": person["name"],
                "Username": person["username"],
                "Email": person["email"],
                "Role": role_label(person["role"]),
                "Department": person["department"],
                "Active": "Yes" if person["active"] else "No",
            }
            for person in people
        ]
    )
    st.subheader("Accounts")
    show_table(display)
    labels = {f"{person['name']} ({person['username']})": person for person in people}
    selected = labels[st.selectbox("Account", list(labels), key="manage-user")]
    st.caption(
        f"{role_label(selected['role'])} · {selected['department']} · "
        f"{'Active' if selected['active'] else 'Inactive'}"
    )
    next_password = st.text_input(
        "New password",
        type="password",
        key=f"reset-{selected['id']}",
        help=f"At least {MIN_PASSWORD_LENGTH} characters.",
    )
    reset_col, state_col = st.columns(2)
    if reset_col.button("Reset password"):
        finish(reset_password(db, user, selected["id"], next_password))
    if selected["active"]:
        if state_col.button("Deactivate account"):
            finish(set_user_active(db, user, selected["id"], False))
    elif state_col.button("Activate account", type="primary"):
        finish(set_user_active(db, user, selected["id"], True))


def leave_types_page(db, user: dict) -> None:
    require(user, ("admin",))
    show_flash()
    page_header("Leave types", "The yearly quota is copied onto each person's balance for the selected year.")
    current = list_leave_types(db, active_only=False)
    if current:
        show_table(
            pd.DataFrame(
                [
                    {
                        "Leave type": item["name"],
                        "Yearly quota": item["yearly_quota"],
                        "Available": "Yes" if item["active"] else "No",
                    }
                    for item in current
                ]
            )
        )
    else:
        st.info("No leave types yet.")

    st.subheader("Add a leave type")
    with st.form("add-leave-type"):
        name = st.text_input("Name", placeholder="Casual Leave")
        quota = st.number_input("Yearly quota", min_value=0, max_value=MAX_QUOTA, value=12, step=1)
        submitted = st.form_submit_button("Add leave type", type="primary")
    if submitted:
        finish(create_leave_type(db, user, name, int(quota)))

    if not current:
        return
    st.subheader("Update a quota")
    labels = {item["name"]: item for item in current}
    selected = labels[st.selectbox("Leave type", list(labels), key="quota-type")]
    quota_col, year_col = st.columns(2)
    with quota_col:
        quota = st.number_input(
            "Quota",
            min_value=0,
            max_value=MAX_QUOTA,
            value=int(selected["yearly_quota"]),
            step=1,
            key=f"quota-value-{selected['id']}",
        )
    with year_col:
        year = year_picker("quota-year")
    action_col, state_col = st.columns(2)
    if action_col.button("Save quota", type="primary"):
        finish(update_quota(db, user, selected["id"], int(quota), year))
    if selected["active"] and state_col.button("Hide from new requests"):
        finish(set_leave_type_active(db, user, selected["id"], False))
    if not selected["active"] and state_col.button("Make available", type="primary"):
        finish(set_leave_type_active(db, user, selected["id"], True))
