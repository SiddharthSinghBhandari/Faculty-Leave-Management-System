"""MongoDB Atlas connection, indexes, and first-run demo data."""

from __future__ import annotations

import os
from datetime import date, timedelta

import certifi
from pymongo import ASCENDING, MongoClient
from pymongo.errors import PyMongoError

from auth import hash_password
from constants import DEMO_PASSWORD
from utils import leave_days, mondays_in_year, now, to_datetime, today

_client: MongoClient | None = None
_signature: tuple[str, str] | None = None
_ready = False


class DatabaseConfigError(Exception):
    """Raised when the Atlas connection string has not been configured."""


def _load_local_env() -> None:
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
    except Exception:
        return


def _secret(name: str) -> str:
    from pathlib import Path

    secrets_file = Path(__file__).resolve().parent / ".streamlit" / "secrets.toml"
    if not secrets_file.exists():
        return ""
    try:
        import streamlit as st

        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def mongo_uri() -> str:
    _load_local_env()
    value = os.getenv("MONGODB_URI", "").strip()
    return value or _secret("MONGODB_URI")


def database_name() -> str:
    _load_local_env()
    value = os.getenv("DATABASE_NAME", "").strip() or _secret("DATABASE_NAME")
    return value or "faculty_leave"


def _uri_is_placeholder(uri: str) -> bool:
    # Atlas hostnames contain the word "cluster", so only the unreplaced template is rejected.
    return any(marker in uri for marker in ("USERNAME", "PASSWORD", "CLUSTER", "<", ">"))


def close_client() -> None:
    global _client, _signature, _ready
    if _client is not None:
        _client.close()
    _client = None
    _signature = None
    _ready = False


def get_client() -> MongoClient:
    global _client, _signature, _ready
    uri = mongo_uri()
    if not uri:
        raise DatabaseConfigError(
            "MONGODB_URI is not set. Copy .env.example to .env and paste the connection string from MongoDB Atlas."
        )
    if _uri_is_placeholder(uri):
        raise DatabaseConfigError(
            "MONGODB_URI still contains the example placeholder. Replace it with the connection string from Atlas."
        )
    name = database_name()
    signature = (uri, name)
    if _client is not None and _signature == signature:
        return _client
    close_client()
    _client = MongoClient(
        uri,
        serverSelectionTimeoutMS=8000,
        tlsCAFile=certifi.where(),
    )
    _signature = signature
    _ready = False
    return _client


def ensure_indexes(db) -> None:
    db.users.create_index("username", unique=True)
    db.users.create_index("email", unique=True)
    db.leave_types.create_index("name", unique=True)
    db.leave_balance.create_index(
        [("user_id", ASCENDING), ("leave_type_id", ASCENDING), ("year", ASCENDING)],
        unique=True,
    )
    db.holidays.create_index("date", unique=True)
    db.leave_requests.create_index([("user_id", ASCENDING), ("status", ASCENDING)])
    db.leave_requests.create_index([("department", ASCENDING), ("status", ASCENDING)])
    db.leave_requests.create_index([("year", ASCENDING), ("status", ASCENDING)])


def get_db():
    global _ready
    client = get_client()
    db = client[database_name()]
    if not _ready:
        client.admin.command("ping")
        ensure_indexes(db)
        seed_if_empty(db)
        _ready = True
    return db


def seed_if_empty(db) -> bool:
    """Insert demo users, quotas, holidays, and sample requests when no users exist."""
    if db.users.count_documents({}) > 0:
        return False

    created: dict[str, list] = {
        "users": [],
        "leave_types": [],
        "holidays": [],
        "leave_balance": [],
        "leave_requests": [],
    }
    try:
        year = today().year
        password_hash = hash_password(DEMO_PASSWORD)
        quotas = (
            ("Casual Leave", 12),
            ("Medical Leave", 10),
            ("Duty Leave", 8),
        )
        type_ids = {}
        quota_by_id = {}
        for name, quota in quotas:
            result = db.leave_types.insert_one(
                {
                    "name": name,
                    "yearly_quota": quota,
                    "active": True,
                    "created_at": now(),
                }
            )
            created["leave_types"].append(result.inserted_id)
            type_ids[name] = result.inserted_id
            quota_by_id[result.inserted_id] = quota

        holiday_dates = set()
        for month, day, description in (
            (1, 26, "Republic Day"),
            (8, 15, "Independence Day"),
            (10, 2, "Gandhi Jayanti"),
            (12, 25, "Christmas"),
        ):
            holiday = date(year, month, day)
            holiday_dates.add(holiday)
            result = db.holidays.insert_one(
                {
                    "date": to_datetime(holiday),
                    "description": description,
                    "created_at": now(),
                }
            )
            created["holidays"].append(result.inserted_id)

        people = (
            ("College Admin", "admin", "admin@college.edu", "admin", "Administration"),
            ("Rakesh Kulkarni", "principal", "principal@college.edu", "principal", "Office of the Principal"),
            ("Sunita Rao", "sunita.rao", "sunita.rao@college.edu", "hod", "Computer Science"),
            ("Amit Verma", "amit.verma", "amit.verma@college.edu", "hod", "Electronics"),
            ("Anita Sharma", "anita.sharma", "anita.sharma@college.edu", "faculty", "Computer Science"),
            ("Rahul Mehta", "rahul.mehta", "rahul.mehta@college.edu", "faculty", "Computer Science"),
            ("Priya Nair", "priya.nair", "priya.nair@college.edu", "faculty", "Electronics"),
        )
        user_ids = {}
        for name, username, email, role, department in people:
            result = db.users.insert_one(
                {
                    "name": name,
                    "username": username,
                    "email": email,
                    "password": password_hash,
                    "role": role,
                    "department": department,
                    "active": True,
                    "created_at": now(),
                }
            )
            created["users"].append(result.inserted_id)
            user_ids[username] = result.inserted_id
            if role == "admin":
                continue
            for type_id, quota in quota_by_id.items():
                balance = db.leave_balance.insert_one(
                    {
                        "user_id": result.inserted_id,
                        "leave_type_id": type_id,
                        "year": year,
                        "total_days": quota,
                        "used_days": 0,
                    }
                )
                created["leave_balance"].append(balance.inserted_id)

        _seed_sample_requests(db, created, year, holiday_dates, type_ids, user_ids)
    except PyMongoError:
        for collection, ids in created.items():
            if ids:
                db[collection].delete_many({"_id": {"$in": ids}})
        raise
    return True


def _seed_sample_requests(db, created, year, holiday_dates, type_ids, user_ids) -> None:
    current = today()
    mondays = mondays_in_year(year)
    past = [day for day in mondays if day + timedelta(days=1) < current]
    future = [day for day in mondays if day > current and day + timedelta(days=2) <= date(year, 12, 31)]

    samples = []
    if past:
        start = past[-1]
        samples.append(
            {
                "username": "anita.sharma",
                "leave_type": "Casual Leave",
                "start": start,
                "end": start + timedelta(days=1),
                "reason": "Family function.",
                "status": "Approved",
                "approver": "sunita.rao",
                "remark": "Approved.",
            }
        )
    if future:
        start = future[0]
        samples.append(
            {
                "username": "rahul.mehta",
                "leave_type": "Medical Leave",
                "start": start,
                "end": start + timedelta(days=2),
                "reason": "Medical appointment and recovery.",
                "status": "Pending",
                "approver": None,
                "remark": "",
            }
        )
    if len(future) > 1:
        start = future[1]
        samples.append(
            {
                "username": "priya.nair",
                "leave_type": "Duty Leave",
                "start": start,
                "end": start + timedelta(days=1),
                "reason": "University evaluation duty.",
                "status": "Pending",
                "approver": None,
                "remark": "",
            }
        )

    names = {
        "sunita.rao": "Sunita Rao",
        "anita.sharma": "Anita Sharma",
        "rahul.mehta": "Rahul Mehta",
        "priya.nair": "Priya Nair",
    }
    departments = {
        "anita.sharma": "Computer Science",
        "rahul.mehta": "Computer Science",
        "priya.nair": "Electronics",
    }
    roles = {
        "anita.sharma": "faculty",
        "rahul.mehta": "faculty",
        "priya.nair": "faculty",
    }

    for sample in samples:
        days = leave_days(sample["start"], sample["end"], holiday_dates)
        if days <= 0:
            continue
        approver_name = ""
        approver_id = None
        decided_at = None
        if sample["status"] == "Approved" and sample["approver"]:
            approver_name = names[sample["approver"]]
            approver_id = user_ids[sample["approver"]]
            decided_at = to_datetime(sample["end"])
            db.leave_balance.update_one(
                {
                    "user_id": user_ids[sample["username"]],
                    "leave_type_id": type_ids[sample["leave_type"]],
                    "year": year,
                },
                {"$inc": {"used_days": days}},
            )
        result = db.leave_requests.insert_one(
            {
                "user_id": user_ids[sample["username"]],
                "applicant_name": names[sample["username"]],
                "applicant_username": sample["username"],
                "applicant_role": roles[sample["username"]],
                "department": departments[sample["username"]],
                "leave_type_id": type_ids[sample["leave_type"]],
                "leave_type_name": sample["leave_type"],
                "year": year,
                "start_date": to_datetime(sample["start"]),
                "end_date": to_datetime(sample["end"]),
                "days": days,
                "reason": sample["reason"],
                "status": sample["status"],
                "remark": sample["remark"],
                "approver_id": approver_id,
                "approver_name": approver_name,
                "created_at": to_datetime(sample["start"]),
                "decided_at": decided_at,
            }
        )
        created["leave_requests"].append(result.inserted_id)
