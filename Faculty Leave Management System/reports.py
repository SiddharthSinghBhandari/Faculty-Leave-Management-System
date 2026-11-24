"""Faculty-wise and month-wise reports as tables, CSV, and PDF."""

from __future__ import annotations

import pandas as pd
from fpdf import FPDF

from utils import MONTHS, format_date, format_when

REQUEST_COLUMNS = [
    "Faculty",
    "Department",
    "Leave type",
    "From",
    "To",
    "Days",
    "Status",
    "Reason",
    "Remark",
    "Decided by",
    "Applied",
]

BALANCE_COLUMNS = ["Faculty", "Department", "Leave type", "Year", "Total", "Used", "Remaining"]

MONTH_COLUMNS = ["Month", "Department", "Leave type", "Requests", "Days"]


def requests_frame(rows: list[dict]) -> pd.DataFrame:
    records = [
        {
            "Faculty": row["applicant"],
            "Department": row["department"],
            "Leave type": row["leave_type"],
            "From": format_date(row["start_date"]),
            "To": format_date(row["end_date"]),
            "Days": row["days"],
            "Status": row["status"],
            "Reason": row["reason"],
            "Remark": row["remark"],
            "Decided by": row["approver"],
            "Applied": format_when(row["created_at"]),
        }
        for row in rows
    ]
    return pd.DataFrame.from_records(records, columns=REQUEST_COLUMNS)


def balances_frame(rows: list[dict]) -> pd.DataFrame:
    records = [
        {
            "Faculty": row["faculty"],
            "Department": row["department"],
            "Leave type": row["leave_type"],
            "Year": row["year"],
            "Total": row["total"],
            "Used": row["used"],
            "Remaining": row["remaining"],
        }
        for row in rows
    ]
    return pd.DataFrame.from_records(records, columns=BALANCE_COLUMNS)


def month_wise_frame(rows: list[dict]) -> pd.DataFrame:
    approved = [row for row in rows if row["status"] == "Approved"]
    if not approved:
        return pd.DataFrame(columns=MONTH_COLUMNS)
    frame = pd.DataFrame(
        {
            "Month": [
                f"{row['start_date'].year}-{row['start_date'].month:02d} {MONTHS[row['start_date'].month - 1]}"
                for row in approved
            ],
            "Department": [row["department"] for row in approved],
            "Leave type": [row["leave_type"] for row in approved],
            "Days": [row["days"] for row in approved],
        }
    )
    grouped = (
        frame.groupby(["Month", "Department", "Leave type"], as_index=False)
        .agg(Requests=("Days", "count"), Days=("Days", "sum"))
        .sort_values(["Month", "Department", "Leave type"])
    )
    return grouped[MONTH_COLUMNS]


def to_csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8-sig")


def _latin(value) -> str:
    text = " ".join(str("" if value is None else value).split())
    return text.encode("latin-1", "replace").decode("latin-1")


def _clip(value, width_mm: float) -> str:
    text = _latin(value)
    max_chars = max(4, int(width_mm / 2.0) - 1)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


class _LeaveReport(FPDF):
    def __init__(self, title: str):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.report_title = _latin(title)
        self.columns: list[str] = []
        self.col_width = 30.0

    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(31, 78, 121)
        self.cell(0, 8, "Faculty Leave Management System", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.set_font("Helvetica", "", 11)
        self.cell(0, 7, self.report_title, align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)
        if self.columns:
            self._write_headers()

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(90, 90, 90)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")

    def _write_headers(self):
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(31, 78, 121)
        self.set_text_color(255, 255, 255)
        self._write_row([_clip(column, self.col_width) for column in self.columns], fill=True)
        self.set_text_color(0, 0, 0)
        self.set_font("Helvetica", "", 8)

    def _write_row(self, values: list[str], fill: bool = False):
        for index, value in enumerate(values):
            last = index == len(values) - 1
            self.cell(
                self.col_width,
                7,
                value,
                border=1,
                fill=fill,
                new_x="LMARGIN" if last else "RIGHT",
                new_y="NEXT" if last else "TOP",
            )

    def write_table(self, frame: pd.DataFrame):
        if frame.empty:
            self.columns = []
            self.add_page()
            self.set_font("Helvetica", "", 11)
            self.cell(0, 8, "No records for the selected filters.", new_x="LMARGIN", new_y="NEXT")
            return
        self.columns = [str(column) for column in frame.columns]
        usable = self.w - self.l_margin - self.r_margin
        self.col_width = usable / max(len(self.columns), 1)
        self.add_page()
        for _, row in frame.iterrows():
            if self.get_y() + 7 > self.h - 16:
                self.add_page()
            self._write_row([_clip(row[column], self.col_width) for column in frame.columns])


def to_pdf_bytes(frame: pd.DataFrame, title: str) -> bytes:
    pdf = _LeaveReport(title)
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(auto=False)
    pdf.write_table(frame)
    return bytes(pdf.output())
