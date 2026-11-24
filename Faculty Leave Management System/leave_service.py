"""Leave rules: apply, cancel, approve, balances, holidays, and admin changes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from auth import hash_password, public_user, role_label, verify_password
from constants import (
    APPROVER_ROLES,
    DEPARTMENTS,
    LEAVE_ROLES,
    MAX_QUOTA,
    MAX_REASON_LENGTH,
    MAX_REMARK_LENGTH,
    MIN_PASSWORD_LENGTH,
    MIN_REASON_LENGTH,
    ROLES,
)
from utils import clean_spaces, format_date, leave_days, now, to_date, to_datetime, today

USERNAME_PATTERN = re.compile(r"^[a-z0-9._]{3,30}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class Result:
    ok: bool
    message: str
    data: Any = None


def parse_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


def _missing() -> Result:
    return Result(False, "That record was not found.")


def request_view(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "user_id": str(document["user_id"]),
        "applicant": document["applicant_name"],
        "username": document["applicant_username"],
        "applicant_role": document["applicant_role"],
        "department": document["department"],
        "leave_type_id": str(document["leave_type_id"]),
        "leave_type": document["leave_type_name"],
        "year": int(document["year"]),
        "start_date": to_date(document["start_date"]),
        "end_date": to_date(document["end_date"]),
        "days": int(document["days"]),
        "reason": document["reason"],
        "status": document["status"],
        "remark": document.get("remark") or "",
        "approver": document.get("approver_name") or "",
        "created_at": document.get("created_at"),
        "decided_at": document.get("decided_at"),
    }


def balance_view(document: dict, user: dict, leave_type_name: str) -> dict:
    total = int(document["total_days"])
    used = int(document["used_days"])
    return {
        "faculty": user["name"],
        "department": user["department"],
        "leave_type": leave_type_name,
        "year": int(document["year"]),
        "total": total,
        "used": used,
        "remaining": total - used,
    }


def list_leave_types(db, active_only: bool = False) -> list[dict]:
    query = {"active": True} if active_only else {}
    rows = []
    for document in db.leave_types.find(query).sort("name", 1):
        rows.append(
            {
                "id": str(document["_id"]),
                "name": document["name"],
                "yearly_quota": int(document["yearly_quota"]),
                "active": bool(document.get("active", True)),
            }
        )
    return rows


def list_holidays(db, year: int) -> list[dict]:
    start = to_datetime(date(year, 1, 1))
    end = to_datetime(date(year, 12, 31))
    rows = []
    for document in db.holidays.find({"date": {"$gte": start, "$lte": end}}).sort("date", 1):
        rows.append(
            {
                "id": str(document["_id"]),
                "date": to_date(document["date"]),
                "description": document["description"],
            }
        )
    return rows


def _holidays_between(db, start: date, end: date) -> list[dict]:
    rows = []
    for document in db.holidays.find(
        {"date": {"$gte": to_datetime(start), "$lte": to_datetime(end)}}
    ).sort("date", 1):
        rows.append({"date": to_date(document["date"]), "description": document["description"]})
    return rows


def department_names(db) -> list[str]:
    stored = db.users.distinct("department")
    names = {clean_spaces(name) for name in stored if name}
    names.update(DEPARTMENTS)
    return sorted(names)


def ensure_balances(db, user_id: str, year: int) -> None:
    oid = parse_id(user_id)
    if oid is None:
        return
    user = db.users.find_one({"_id": oid})
    if user is None or user.get("role") not in LEAVE_ROLES:
        return
    for leave_type in db.leave_types.find({"active": True}):
        db.leave_balance.update_one(
            {"user_id": oid, "leave_type_id": leave_type["_id"], "year": year},
            {
                "$setOnInsert": {
                    "total_days": int(leave_type["yearly_quota"]),
                    "used_days": 0,
                }
            },
            upsert=True,
        )


def balances_for_user(db, user: dict, year: int) -> list[dict]:
    ensure_balances(db, user["id"], year)
    oid = parse_id(user["id"])
    names = {item["_id"]: item["name"] for item in db.leave_types.find()}
    rows = []
    for document in db.leave_balance.find({"user_id": oid, "year": year}):
        rows.append(balance_view(document, user, names.get(document["leave_type_id"], "Leave")))
    rows.sort(key=lambda item: item["leave_type"])
    return rows


def my_requests(db, user: dict) -> list[dict]:
    oid = parse_id(user["id"])
    documents = db.leave_requests.find({"user_id": oid}).sort("created_at", -1)
    return [request_view(document) for document in documents]


def _evaluate(db, user: dict, leave_type_id: str, start: date, end: date) -> Result:
    if user["role"] not in LEAVE_ROLES:
        return Result(False, "This role cannot apply for leave.")
    if end < start:
        return Result(False, "The end date has to be on or after the start date.")
    if start.year != end.year:
        return Result(False, "Submit a separate request for each calendar year.")
    if abs(start.year - today().year) > 1:
        return Result(False, "Choose dates in the previous year, this year, or the next year.")

    type_id = parse_id(leave_type_id)
    leave_type = db.leave_types.find_one({"_id": type_id, "active": True}) if type_id else None
    if leave_type is None:
        return Result(False, "Select an active leave type.")

    holidays = _holidays_between(db, start, end)
    holiday_dates = {item["date"] for item in holidays}
    days = leave_days(start, end, holiday_dates)
    if days <= 0:
        return Result(False, "Every date in this range is a holiday, so there is no leave to deduct.")

    year = start.year
    ensure_balances(db, user["id"], year)
    balance = db.leave_balance.find_one(
        {
            "user_id": parse_id(user["id"]),
            "leave_type_id": leave_type["_id"],
            "year": year,
        }
    )
    if balance is None:
        return Result(False, "No leave balance is available for this year.")
    remaining = int(balance["total_days"]) - int(balance["used_days"])
    if days > remaining:
        return Result(
            False,
            f"{leave_type['name']} has {remaining} day(s) left in {year}. This request needs {days}.",
        )

    overlap = db.leave_requests.find_one(
        {
            "user_id": parse_id(user["id"]),
            "status": {"$in": ["Pending", "Approved"]},
            "start_date": {"$lte": to_datetime(end)},
            "end_date": {"$gte": to_datetime(start)},
        }
    )
    if overlap is not None:
        return Result(
            False,
            "These dates overlap a "
            f"{overlap['status'].lower()} {overlap['leave_type_name']} request "
            f"from {format_date(to_date(overlap['start_date']))} to {format_date(to_date(overlap['end_date']))}.",
        )

    if holidays:
        excluded = ", ".join(f"{format_date(item['date'])} ({item['description']})" for item in holidays)
        holiday_text = f" Excluded holidays: {excluded}."
    else:
        holiday_text = " No holidays fall in this range."
    message = (
        f"{days} leave day(s) will be deducted for {leave_type['name']}.{holiday_text} "
        f"Balance left after approval: {remaining - days}."
    )
    return Result(
        True,
        message,
        {
            "leave_type": leave_type,
            "days": days,
            "remaining": remaining,
            "year": year,
        },
    )


def preview_leave(db, user: dict, leave_type_id: str, start: date, end: date) -> Result:
    return _evaluate(db, user, leave_type_id, start, end)


def apply_leave(db, user: dict, leave_type_id: str, start: date, end: date, reason: str) -> Result:
    reason = clean_spaces(reason)
    if len(reason) < MIN_REASON_LENGTH:
        return Result(False, f"Enter a reason of at least {MIN_REASON_LENGTH} characters.")
    if len(reason) > MAX_REASON_LENGTH:
        return Result(False, f"Keep the reason within {MAX_REASON_LENGTH} characters.")

    checked = _evaluate(db, user, leave_type_id, start, end)
    if not checked.ok:
        return checked

    details = checked.data
    leave_type = details["leave_type"]
    db.leave_requests.insert_one(
        {
            "user_id": parse_id(user["id"]),
            "applicant_name": user["name"],
            "applicant_username": user["username"],
            "applicant_role": user["role"],
            "department": user["department"],
            "leave_type_id": leave_type["_id"],
            "leave_type_name": leave_type["name"],
            "year": details["year"],
            "start_date": to_datetime(start),
            "end_date": to_datetime(end),
            "days": details["days"],
            "reason": reason,
            "status": "Pending",
            "remark": "",
            "approver_id": None,
            "approver_name": "",
            "created_at": now(),
            "decided_at": None,
        }
    )
    return Result(True, f"Leave request submitted for {details['days']} day(s). Status: Pending.")


def cancel_request(db, user: dict, request_id: str) -> Result:
    oid = parse_id(request_id)
    if oid is None:
        return _missing()
    updated = db.leave_requests.update_one(
        {"_id": oid, "user_id": parse_id(user["id"]), "status": "Pending"},
        {"$set": {"status": "Cancelled", "decided_at": now(), "remark": "Cancelled by the applicant."}},
    )
    if updated.modified_count != 1:
        return Result(False, "Only your own pending request can be cancelled.")
    return Result(True, "The pending request was cancelled. The leave balance was not changed.")


def _can_decide(approver: dict, document: dict) -> bool:
    if approver["role"] not in APPROVER_ROLES:
        return False
    if str(document["user_id"]) == approver["id"]:
        return False
    if approver["role"] == "hod":
        return document.get("department") == approver["department"] and document.get("applicant_role") == "faculty"
    return True


def pending_requests(db, approver: dict) -> list[dict]:
    if approver["role"] not in APPROVER_ROLES:
        return []
    query: dict[str, Any] = {
        "status": "Pending",
        "user_id": {"$ne": parse_id(approver["id"])},
    }
    if approver["role"] == "hod":
        query["department"] = approver["department"]
        query["applicant_role"] = "faculty"
    documents = db.leave_requests.find(query).sort("created_at", 1)
    return [request_view(document) for document in documents]


def decide_request(db, approver: dict, request_id: str, approve: bool, remark: str) -> Result:
    oid = parse_id(request_id)
    if oid is None:
        return _missing()
    remark = clean_spaces(remark)
    if len(remark) > MAX_REMARK_LENGTH:
        return Result(False, f"Keep the remark within {MAX_REMARK_LENGTH} characters.")
    if not approve and len(remark) < 3:
        return Result(False, "Add a remark when rejecting a request.")

    document = db.leave_requests.find_one({"_id": oid})
    if document is None:
        return _missing()
    if document.get("status") != "Pending":
        return Result(False, "This request is no longer pending.")
    if not _can_decide(approver, document):
        return Result(False, "You cannot decide this request.")

    if not approve:
        updated = db.leave_requests.update_one(
            {"_id": oid, "status": "Pending"},
            {
                "$set": {
                    "status": "Rejected",
                    "remark": remark,
                    "approver_id": parse_id(approver["id"]),
                    "approver_name": approver["name"],
                    "decided_at": now(),
                }
            },
        )
        if updated.modified_count != 1:
            return Result(False, "This request was already decided.")
        return Result(True, "The request was rejected. The leave balance was not changed.")

    start = to_date(document["start_date"])
    end = to_date(document["end_date"])
    holidays = {item["date"] for item in _holidays_between(db, start, end)}
    days = leave_days(start, end, holidays)
    if days <= 0:
        return Result(False, "This range is entirely holidays, so it cannot be approved.")

    year = int(document["year"])
    ensure_balances(db, str(document["user_id"]), year)
    balance_filter = {
        "user_id": document["user_id"],
        "leave_type_id": document["leave_type_id"],
        "year": year,
        "$expr": {"$lte": [{"$add": ["$used_days", days]}, "$total_days"]},
    }
    deducted = db.leave_balance.update_one(balance_filter, {"$inc": {"used_days": days}})
    if deducted.modified_count != 1:
        return Result(False, "The remaining balance is not enough to approve this request.")

    approved = db.leave_requests.update_one(
        {"_id": oid, "status": "Pending"},
        {
            "$set": {
                "status": "Approved",
                "days": days,
                "remark": remark or "Approved.",
                "approver_id": parse_id(approver["id"]),
                "approver_name": approver["name"],
                "decided_at": now(),
            }
        },
    )
    if approved.modified_count != 1:
        db.leave_balance.update_one(
            {
                "user_id": document["user_id"],
                "leave_type_id": document["leave_type_id"],
                "year": year,
            },
            {"$inc": {"used_days": -days}},
        )
        return Result(False, "This request was already decided.")
    return Result(True, f"Approved. {days} day(s) were deducted from the leave balance.")


def visible_requests(db, viewer: dict, year: int, department: str | None, status: str | None) -> list[dict]:
    query: dict[str, Any] = {"year": year}
    if status:
        query["status"] = status
    if viewer["role"] == "hod":
        query["department"] = viewer["department"]
    elif viewer["role"] in ("principal", "admin"):
        if department:
            query["department"] = department
    else:
        query["user_id"] = parse_id(viewer["id"])
    documents = db.leave_requests.find(query).sort("created_at", -1)
    return [request_view(document) for document in documents]


def department_balances(db, viewer: dict, year: int, department: str | None) -> list[dict]:
    query: dict[str, Any] = {"active": True, "role": {"$in": list(LEAVE_ROLES)}}
    if viewer["role"] == "hod":
        query["department"] = viewer["department"]
    elif department:
        query["department"] = department
    elif viewer["role"] not in ("principal", "admin"):
        query["_id"] = parse_id(viewer["id"])

    rows = []
    for document in db.users.find(query).sort("name", 1):
        person = public_user(document)
        rows.extend(balances_for_user(db, person, year))
    return rows


def list_users(db) -> list[dict]:
    rows = []
    for document in db.users.find({}, {"password": 0}).sort("name", 1):
        record = public_user(document)
        record["active"] = bool(document.get("active", True))
        rows.append(record)
    return rows


def create_user(
    db,
    actor: dict,
    name: str,
    username: str,
    email: str,
    password: str,
    role: str,
    department: str,
) -> Result:
    if actor["role"] != "admin":
        return Result(False, "Only an admin can add users.")
    name = clean_spaces(name)
    username = username.strip().lower()
    email = email.strip().lower()
    department = clean_spaces(department)
    if len(name) < 2:
        return Result(False, "Enter the full name.")
    if USERNAME_PATTERN.match(username) is None:
        return Result(False, "Username must be 3–30 characters: lowercase letters, numbers, dots, or underscores.")
    if EMAIL_PATTERN.match(email) is None:
        return Result(False, "Enter a valid email address.")
    if len(password) < MIN_PASSWORD_LENGTH:
        return Result(False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if role not in ROLES:
        return Result(False, "Select a valid role.")
    if not department:
        return Result(False, "Select a department.")

    try:
        result = db.users.insert_one(
            {
                "name": name,
                "username": username,
                "email": email,
                "password": hash_password(password),
                "role": role,
                "department": department,
                "active": True,
                "created_at": now(),
            }
        )
    except DuplicateKeyError:
        return Result(False, "That username or email is already in use.")

    if role in LEAVE_ROLES:
        ensure_balances(db, str(result.inserted_id), today().year)
    return Result(True, f"{name} was added as {role_label(role)}.")


def set_user_active(db, actor: dict, user_id: str, active: bool) -> Result:
    if actor["role"] != "admin":
        return Result(False, "Only an admin can change accounts.")
    oid = parse_id(user_id)
    if oid is None:
        return _missing()
    if user_id == actor["id"] and not active:
        return Result(False, "You cannot deactivate your own account.")
    target = db.users.find_one({"_id": oid})
    if target is None:
        return _missing()
    if target.get("role") == "admin" and not active:
        others = db.users.count_documents({"role": "admin", "active": True, "_id": {"$ne": oid}})
        if others < 1:
            return Result(False, "Keep at least one active admin account.")
    db.users.update_one({"_id": oid}, {"$set": {"active": active}})
    state = "activated" if active else "deactivated"
    return Result(True, f"{target['name']} was {state}.")


def reset_password(db, actor: dict, user_id: str, password: str) -> Result:
    if actor["role"] != "admin":
        return Result(False, "Only an admin can reset a password.")
    if len(password) < MIN_PASSWORD_LENGTH:
        return Result(False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    oid = parse_id(user_id)
    if oid is None:
        return _missing()
    updated = db.users.update_one({"_id": oid}, {"$set": {"password": hash_password(password)}})
    if updated.matched_count != 1:
        return _missing()
    return Result(True, "Password updated.")


def change_own_password(db, actor: dict, current_password: str, new_password: str) -> Result:
    """Allow any signed-in user to change only their own password."""
    oid = parse_id(actor.get("id", ""))
    if oid is None:
        return _missing()

    document = db.users.find_one({"_id": oid, "active": True})
    if document is None:
        return Result(False, "Your account could not be found or is inactive. Please sign in again.")

    if not current_password or not verify_password(current_password, document.get("password", "")):
        return Result(False, "Your current password is incorrect.")

    if len(new_password) < MIN_PASSWORD_LENGTH:
        return Result(False, f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")

    if verify_password(new_password, document.get("password", "")):
        return Result(False, "Your new password must be different from your current password.")

    updated = db.users.update_one(
        {"_id": oid, "active": True},
        {"$set": {"password": hash_password(new_password)}},
    )
    if updated.modified_count != 1:
        return Result(False, "Password was not changed. Please try again.")
    return Result(True, "Your password has been changed successfully.")


def create_leave_type(db, actor: dict, name: str, quota: int) -> Result:
    if actor["role"] != "admin":
        return Result(False, "Only an admin can add leave types.")
    name = clean_spaces(name)
    if len(name) < 3:
        return Result(False, "Enter a leave type name.")
    if not 0 <= int(quota) <= MAX_QUOTA:
        return Result(False, f"Yearly quota must be between 0 and {MAX_QUOTA}.")
    existing = db.leave_types.find_one({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
    if existing is not None:
        return Result(False, "That leave type already exists.")
    try:
        result = db.leave_types.insert_one(
            {"name": name, "yearly_quota": int(quota), "active": True, "created_at": now()}
        )
    except DuplicateKeyError:
        return Result(False, "That leave type already exists.")
    year = today().year
    for user in db.users.find({"active": True, "role": {"$in": list(LEAVE_ROLES)}}):
        db.leave_balance.update_one(
            {"user_id": user["_id"], "leave_type_id": result.inserted_id, "year": year},
            {"$setOnInsert": {"total_days": int(quota), "used_days": 0}},
            upsert=True,
        )
    return Result(True, f"{name} was added with a yearly quota of {int(quota)}.")


def update_quota(db, actor: dict, leave_type_id: str, quota: int, year: int) -> Result:
    if actor["role"] != "admin":
        return Result(False, "Only an admin can change quotas.")
    if not 0 <= int(quota) <= MAX_QUOTA:
        return Result(False, f"Yearly quota must be between 0 and {MAX_QUOTA}.")
    oid = parse_id(leave_type_id)
    leave_type = db.leave_types.find_one({"_id": oid}) if oid else None
    if leave_type is None:
        return _missing()
    db.leave_types.update_one({"_id": oid}, {"$set": {"yearly_quota": int(quota)}})
    for user in db.users.find({"role": {"$in": list(LEAVE_ROLES)}}):
        ensure_balances(db, str(user["_id"]), year)
    db.leave_balance.update_many(
        {"leave_type_id": oid, "year": year},
        {"$set": {"total_days": int(quota)}},
    )
    return Result(True, f"{leave_type['name']} quota for {year} is now {int(quota)} day(s).")


def set_leave_type_active(db, actor: dict, leave_type_id: str, active: bool) -> Result:
    if actor["role"] != "admin":
        return Result(False, "Only an admin can change leave types.")
    oid = parse_id(leave_type_id)
    if oid is None:
        return _missing()
    updated = db.leave_types.update_one({"_id": oid}, {"$set": {"active": active}})
    if updated.matched_count != 1:
        return _missing()
    state = "available" if active else "hidden"
    return Result(True, f"The leave type is now {state} for new requests.")


def add_holiday(db, actor: dict, holiday: date, description: str) -> Result:
    if actor["role"] != "admin":
        return Result(False, "Only an admin can add holidays.")
    description = clean_spaces(description)
    if len(description) < 3:
        return Result(False, "Enter a short description for the holiday.")
    try:
        db.holidays.insert_one(
            {"date": to_datetime(holiday), "description": description, "created_at": now()}
        )
    except DuplicateKeyError:
        return Result(False, "A holiday is already recorded on that date.")
    return Result(True, f"{format_date(holiday)} was added to the holiday list.")


def delete_holiday(db, actor: dict, holiday_id: str) -> Result:
    if actor["role"] != "admin":
        return Result(False, "Only an admin can remove holidays.")
    oid = parse_id(holiday_id)
    if oid is None:
        return _missing()
    deleted = db.holidays.delete_one({"_id": oid})
    if deleted.deleted_count != 1:
        return _missing()
    return Result(True, "Holiday removed. Pending requests are counted again when they are approved.")


def admin_counts(db) -> dict:
    year = today().year
    start = to_datetime(date(year, 1, 1))
    end = to_datetime(date(year, 12, 31))
    return {
        "users": db.users.count_documents({"active": True}),
        "pending": db.leave_requests.count_documents({"status": "Pending"}),
        "leave_types": db.leave_types.count_documents({"active": True}),
        "holidays": db.holidays.count_documents({"date": {"$gte": start, "$lte": end}}),
    }

