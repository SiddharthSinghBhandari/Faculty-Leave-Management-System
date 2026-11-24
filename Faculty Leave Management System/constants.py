"""Roles, departments, and limits used across the leave system."""

ROLES = ("faculty", "hod", "principal", "admin")

ROLE_LABELS = {
    "faculty": "Faculty",
    "hod": "Head of Department",
    "principal": "Principal",
    "admin": "Admin",
}

# Faculty, HoD, and Principal can apply. Admin manages the system.
LEAVE_ROLES = ("faculty", "hod", "principal")
APPROVER_ROLES = ("hod", "principal")

DEPARTMENTS = (
    "Computer Science",
    "Electronics",
    "Mechanical Engineering",
    "Mathematics",
    "Office of the Principal",
    "Administration",
)

REQUEST_STATUSES = ("Pending", "Approved", "Rejected", "Cancelled")

DEMO_PASSWORD = "demo123"
MIN_PASSWORD_LENGTH = 6
MIN_REASON_LENGTH = 5
MAX_REASON_LENGTH = 500
MAX_REMARK_LENGTH = 300
MAX_QUOTA = 366
