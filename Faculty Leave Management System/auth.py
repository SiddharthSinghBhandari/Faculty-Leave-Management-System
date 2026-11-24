"""Password hashing and the signed-in user record."""

import bcrypt

from constants import ROLE_LABELS


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def public_user(doc: dict) -> dict:
    """Drop the password hash before the record reaches the interface."""
    return {
        "id": str(doc["_id"]),
        "name": doc["name"],
        "username": doc["username"],
        "email": doc["email"],
        "role": doc["role"],
        "department": doc.get("department") or "",
    }


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def authenticate(db, username: str, password: str) -> dict | None:
    username = username.strip().lower()
    if not username or not password:
        return None
    document = db.users.find_one({"username": username, "active": True})
    if document is None or not verify_password(password, document.get("password", "")):
        return None
    return public_user(document)


def reload_user(db, user_id: str) -> dict | None:
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        document = db.users.find_one({"_id": ObjectId(user_id), "active": True})
    except InvalidId:
        return None
    if document is None:
        return None
    return public_user(document)
