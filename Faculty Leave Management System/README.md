# Faculty Leave Management System

A college leave desk in the browser. Faculty apply for leave, the Head of Department or the Principal approves it, and MongoDB Atlas stores every record. Leave days are counted from the dates on the request, skipping the holiday list, and the balance changes only when a request is approved.

This matches the project synopsis: Streamlit for the interface, PyMongo for Atlas, bcrypt for passwords, and pandas for CSV reports. PDF downloads use FPDF2.

## What each role can do

| Role | Can do |
| --- | --- |
| Faculty | Apply, see balance, track requests, cancel a pending request, read the holiday list |
| Head of Department | Decide pending requests from faculty in their own department, and read that department's history |
| Principal | Decide any pending request except their own, read college-wide history, download reports |
| Admin | Add and deactivate users, set leave types and yearly quotas, maintain holidays, download reports |

HoD and Principal can also apply for their own leave. A Head of Department does not approve their own request. The Principal does.

## Leave rules

- A request is stored with status `Pending`.
- Overlapping pending or approved dates are rejected.
- A request cannot cross into the next calendar year.
- Holiday dates inside the range are not deducted. Other dates, including weekly offs, are deducted unless an admin adds them to the holiday list.
- On approval the working count is calculated again, then `used days` increases.
- Remaining balance = yearly quota − used days.
- Rejection and cancellation do not change the balance.

## Collections

| Collection | Contents |
| --- | --- |
| `users` | Name, email, bcrypt password, role, department |
| `leave_types` | Name and yearly quota |
| `leave_balance` | User, leave type, year, total days, used days |
| `holidays` | Date and description |
| `leave_requests` | The application itself: dates, reason, status, and the approver's remark |

`leave_requests` is the transaction record the workflow needs. The synopsis lists the master data; each application is stored as its own document so status and history do not require joins.

## Run it

Python 3.11 or newer.

```powershell
cd C:\Users\SiddharthBhandari\faculty-leave-management
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
notepad .env
streamlit run app.py
```

In `.env`:

```
MONGODB_URI=mongodb+srv://USER:PASSWORD@cluster0.example.mongodb.net/?retryWrites=true&w=majority
DATABASE_NAME=faculty_leave
```

Atlas setup:

1. Create a free cluster and a database user.
2. Network Access → add your current IP.
3. Connect → Drivers → Python, and copy the connection string.
4. URL-encode any `@ : / ? #` characters in the password.

For Streamlit Community Cloud, put the same values in `.streamlit/secrets.toml` (see `secrets.toml.example`).

The first successful launch seeds demo data **only if the `users` collection is empty**. To seed again, drop the `faculty_leave` database in Atlas and restart the app.

## Demo sign-in

Password for every demo account: `demo123`

| Username | Role |
| --- | --- |
| `admin` | Admin |
| `principal` | Principal |
| `sunita.rao` | HoD, Computer Science |
| `amit.verma` | HoD, Electronics |
| `anita.sharma` | Faculty, Computer Science |
| `rahul.mehta` | Faculty, Computer Science |
| `priya.nair` | Faculty, Electronics |

Sample requests are created for the current year: one approved casual leave, plus pending requests so each HoD has something in the queue when those dates exist.

## Project files

| File | Responsibility |
| --- | --- |
| `app.py` | Sign-in, role menu, Atlas setup screen |
| `views.py` | Dashboards, forms, and reports |
| `leave_service.py` | Validation, balance updates, approval |
| `reports.py` | Tables, CSV, and PDF |
| `database.py` | Connection, indexes, demo seed |
| `auth.py` | bcrypt hash and check |
| `constants.py` | Roles, quotas limits, demo password |
| `utils.py` | Dates and the holiday-day count |

## Reports

Admin and Principal can download:

- Faculty-wise balance (total, used, remaining)
- Month-wise approved leave (request count and days)
- A request register for the selected year, department, and status

CSV is UTF-8 with a BOM so Excel on Windows opens it cleanly. The PDF is a landscape summary; long reasons are kept in the CSV.
