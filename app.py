"""
AI Student Attendance Analyzer
==============================
A beginner-friendly Streamlit application that lets you:

* manage students and classes (SQLite database),
* mark and update daily attendance,
* analyse attendance with Pandas / NumPy,
* visualise results with Plotly,
* import / export CSV and Excel files,
* ask Google Gemini questions about your REAL attendance data.

Run with:  streamlit run app.py
"""

import io
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from dateutil import parser as date_parser

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
APP_TITLE = "AI Student Attendance Analyzer"
DB_PATH = os.environ.get("ATTENDANCE_DB_PATH", "attendance.db")
STATUSES = ["Present", "Late", "Absent"]
ATTENDED_STATUSES = ["Present", "Late"]  # "Late" still counts as attending
DEFAULT_THRESHOLD = 75
MAX_UPLOAD_MB = 5
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
MAX_AI_ROWS = 40  # limits how much data is sent to Gemini per table

PRIMARY_COLOR = "#3B5BDB"
STATUS_COLORS = {"Present": "#2E8B57", "Late": "#F0A202", "Absent": "#D64550"}

PAGES = [
    "📊 Dashboard",
    "🎓 Students",
    "📝 Mark Attendance",
    "🗂️ Attendance Records",
    "📈 Analysis & Charts",
    "📁 Import / Export",
    "🤖 AI Assistant",
]

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]{0,29}$")

# Accepted column names in uploaded files (after lower-casing and removing
# spaces / symbols).  Example: "Roll No." -> "rollno" -> student_id
COLUMN_ALIASES = {
    "student_id": ["studentid", "id", "rollno", "rollnumber", "roll", "studentroll"],
    "name": ["name", "studentname", "fullname"],
    "class": ["class", "section", "classsection", "classname"],
    "date": ["date", "attendancedate"],
    "status": ["status", "attendance", "attendancestatus"],
}
STATUS_ALIASES = {
    "present": "Present",
    "p": "Present",
    "absent": "Absent",
    "a": "Absent",
    "late": "Late",
    "l": "Late",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    class_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_pk INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    attendance_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Present', 'Absent', 'Late')),
    UNIQUE (student_pk, attendance_date)
);
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance (attendance_date);
CREATE INDEX IF NOT EXISTS idx_students_class ON students (class_name);
"""


# ---------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------
@contextmanager
def db_connection():
    """Open a SQLite connection; commit on success, roll back on error."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables automatically when the app starts."""
    with db_connection() as conn:
        conn.executescript(SCHEMA)


def ensure_class(conn, class_name):
    """Insert the class if it is new and return its stored spelling."""
    conn.execute("INSERT OR IGNORE INTO classes (name) VALUES (?)", (class_name,))
    row = conn.execute("SELECT name FROM classes WHERE name = ?", (class_name,)).fetchone()
    return row["name"] if row else class_name


def cleanup_classes(conn):
    """Remove classes that no longer have any students."""
    conn.execute(
        "DELETE FROM classes WHERE name NOT IN (SELECT DISTINCT class_name FROM students)"
    )


def validate_student_fields(student_id, name, class_name):
    """Clean and validate student input. Returns (student_id, name, class, errors)."""
    student_id = (student_id or "").strip()
    name = " ".join((name or "").split())
    class_name = " ".join((class_name or "").split())
    errors = []
    if not student_id:
        errors.append("Student ID / roll number is required.")
    elif not ID_PATTERN.match(student_id):
        errors.append(
            "Student ID may only contain letters, numbers, and - _ . / "
            "(max 30 characters, no spaces)."
        )
    if not name:
        errors.append("Student name is required.")
    elif len(name) > 100:
        errors.append("Student name is too long (max 100 characters).")
    if not class_name:
        errors.append("Class / section is required.")
    elif len(class_name) > 50:
        errors.append("Class / section is too long (max 50 characters).")
    return student_id, name, class_name, errors


def add_student(student_id, name, class_name):
    """Add one student. Returns (success, message)."""
    student_id, name, class_name, errors = validate_student_fields(student_id, name, class_name)
    if errors:
        return False, " ".join(errors)
    try:
        with db_connection() as conn:
            stored_class = ensure_class(conn, class_name)
            conn.execute(
                "INSERT INTO students (student_id, name, class_name) VALUES (?, ?, ?)",
                (student_id, name, stored_class),
            )
        return True, f"Student '{name}' ({student_id}) was added successfully."
    except sqlite3.IntegrityError:
        return False, f"Student ID '{student_id}' already exists. Student IDs must be unique."
    except sqlite3.Error as exc:
        return False, f"Database error while adding the student: {exc}"


def update_student(pk, student_id, name, class_name):
    """Update one student. Returns (success, message)."""
    student_id, name, class_name, errors = validate_student_fields(student_id, name, class_name)
    if errors:
        return False, " ".join(errors)
    try:
        with db_connection() as conn:
            stored_class = ensure_class(conn, class_name)
            cursor = conn.execute(
                "UPDATE students SET student_id = ?, name = ?, class_name = ? WHERE id = ?",
                (student_id, name, stored_class, int(pk)),
            )
            if cursor.rowcount == 0:
                return False, "That student no longer exists."
            cleanup_classes(conn)
        return True, f"Student '{name}' was updated successfully."
    except sqlite3.IntegrityError:
        return False, f"Student ID '{student_id}' is already used by another student."
    except sqlite3.Error as exc:
        return False, f"Database error while updating the student: {exc}"


def delete_student(pk):
    """Delete a student and all of their attendance records."""
    try:
        with db_connection() as conn:
            cursor = conn.execute("DELETE FROM students WHERE id = ?", (int(pk),))
            if cursor.rowcount == 0:
                return False, "That student no longer exists."
            cleanup_classes(conn)
        return True, "Student and related attendance records were deleted."
    except sqlite3.Error as exc:
        return False, f"Database error while deleting the student: {exc}"


def save_attendance(date_str, records):
    """Save (student_pk, status) pairs for one date. Existing entries are updated."""
    if not records:
        return False, "There are no students to save."
    cleaned = []
    for pk, status in records:
        if status not in STATUSES:
            return False, f"Invalid status '{status}'. Use Present, Absent, or Late."
        cleaned.append((int(pk), date_str, status))
    try:
        with db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO attendance (student_pk, attendance_date, status)
                VALUES (?, ?, ?)
                ON CONFLICT (student_pk, attendance_date)
                DO UPDATE SET status = excluded.status
                """,
                cleaned,
            )
        return True, f"Attendance for {date_str} saved ({len(cleaned)} students)."
    except sqlite3.Error as exc:
        return False, f"Database error while saving attendance: {exc}"


def delete_attendance_for_day(date_str, class_name):
    """Delete all attendance rows of one class on one date."""
    try:
        with db_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM attendance
                WHERE attendance_date = ?
                  AND student_pk IN (SELECT id FROM students WHERE class_name = ?)
                """,
                (date_str, class_name),
            )
        return True, f"Deleted {cursor.rowcount} attendance record(s)."
    except sqlite3.Error as exc:
        return False, f"Database error while deleting attendance: {exc}"


STUDENT_COLUMNS = ["pk", "Student ID", "Name", "Class"]
ATTENDANCE_COLUMNS = ["pk", "Student ID", "Name", "Class", "Date", "Status", "Month"]


def load_students():
    """Return all students as a DataFrame (never raises)."""
    try:
        with db_connection() as conn:
            df = pd.read_sql_query(
                """
                SELECT id AS pk, student_id AS "Student ID", name AS "Name",
                       class_name AS "Class"
                FROM students ORDER BY class_name, name
                """,
                conn,
            )
        return df
    except Exception as exc:  # noqa: BLE001 - show a friendly message instead of crashing
        st.error(f"Could not read students from the database: {exc}")
        return pd.DataFrame(columns=STUDENT_COLUMNS)


def load_attendance():
    """Return all attendance records joined with student info (never raises)."""
    try:
        with db_connection() as conn:
            df = pd.read_sql_query(
                """
                SELECT a.student_pk AS pk, s.student_id AS "Student ID", s.name AS "Name",
                       s.class_name AS "Class", a.attendance_date AS "Date",
                       a.status AS "Status"
                FROM attendance a JOIN students s ON s.id = a.student_pk
                ORDER BY a.attendance_date DESC, s.class_name, s.name
                """,
                conn,
            )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read attendance from the database: {exc}")
        df = pd.DataFrame(columns=ATTENDANCE_COLUMNS[:-1])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).copy()
    df["Month"] = df["Date"].dt.strftime("%Y-%m")
    return df[ATTENDANCE_COLUMNS]


# ---------------------------------------------------------------------------
# Analysis (Pandas + NumPy)
# ---------------------------------------------------------------------------
def percentage(attended, total):
    """Safe percentage that returns NaN when total is zero."""
    attended = np.asarray(attended, dtype=float)
    total = np.asarray(total, dtype=float)
    return np.round(
        np.divide(attended * 100.0, total, out=np.full(attended.shape, np.nan), where=total > 0),
        2,
    )


def overall_percentage(att):
    """Overall attendance % (Present + Late) or None when there is no data."""
    if att.empty:
        return None
    attended = int(att["Status"].isin(ATTENDED_STATUSES).sum())
    return float(percentage(attended, len(att)))


def group_report(att, group_cols):
    """Count statuses per group and compute the attendance percentage."""
    columns = list(group_cols) + STATUSES + ["Total", "Attendance %"]
    if att.empty:
        return pd.DataFrame(columns=columns)
    counts = (
        att.groupby(group_cols)["Status"]
        .value_counts()
        .unstack(fill_value=0)
        .reindex(columns=STATUSES, fill_value=0)
    )
    counts["Total"] = counts[STATUSES].sum(axis=1)
    counts["Attendance %"] = percentage(counts["Present"] + counts["Late"], counts["Total"])
    return counts.reset_index()[columns]


def student_summary(students, att, threshold):
    """Per-student totals, attendance % and a low-attendance flag."""
    columns = [
        "Student ID", "Name", "Class", "Present", "Late", "Absent",
        "Total Days", "Attendance %", "Low Attendance",
    ]
    if students.empty:
        return pd.DataFrame(columns=columns)
    base = students[["pk", "Student ID", "Name", "Class"]].copy()
    if att.empty:
        counts = pd.DataFrame(0, index=base["pk"], columns=STATUSES)
    else:
        counts = (
            att.groupby("pk")["Status"]
            .value_counts()
            .unstack(fill_value=0)
            .reindex(columns=STATUSES, fill_value=0)
        )
    base = base.merge(counts, how="left", left_on="pk", right_index=True)
    base[STATUSES] = base[STATUSES].fillna(0).astype(int)
    base["Total Days"] = base[STATUSES].sum(axis=1)
    base["Attendance %"] = percentage(base["Present"] + base["Late"], base["Total Days"])
    base["Low Attendance"] = base["Attendance %"] < threshold  # NaN compares as False
    return base[columns].reset_index(drop=True)


def daily_report(att):
    report = group_report(att, ["Date"])
    if not report.empty:
        report["Date"] = report["Date"].dt.date
        report = report.sort_values("Date", ascending=False).reset_index(drop=True)
    return report


def monthly_report(att):
    report = group_report(att, ["Month"])
    if not report.empty:
        report = report.sort_values("Month").reset_index(drop=True)
    return report


def class_report(att):
    report = group_report(att, ["Class"])
    if not report.empty:
        report = report.sort_values("Class").reset_index(drop=True)
    return report


# ---------------------------------------------------------------------------
# Charts (Plotly)
# ---------------------------------------------------------------------------
def style_figure(fig, height=360):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=50, b=10), title_x=0.02)
    return fig


def chart_status_split(att, title="Present vs Late vs Absent"):
    if att.empty:
        return None
    counts = att["Status"].value_counts().reindex(STATUSES, fill_value=0).reset_index()
    counts.columns = ["Status", "Records"]
    fig = px.pie(
        counts, names="Status", values="Records", hole=0.5, title=title,
        color="Status", color_discrete_map=STATUS_COLORS,
    )
    return style_figure(fig)


def chart_monthly_trend(att, title="Monthly attendance trend"):
    report = monthly_report(att)
    if report.empty:
        return None
    fig = px.line(report, x="Month", y="Attendance %", markers=True, title=title)
    fig.update_traces(line_color=PRIMARY_COLOR)
    fig.update_yaxes(range=[0, 100])
    fig.update_xaxes(type="category")
    return style_figure(fig)


def chart_class_comparison(att, title="Class-wise attendance comparison"):
    report = class_report(att)
    if report.empty:
        return None
    fig = px.bar(report, x="Class", y="Attendance %", text="Attendance %", title=title)
    fig.update_traces(marker_color=PRIMARY_COLOR, textposition="outside")
    fig.update_yaxes(range=[0, 110])
    return style_figure(fig)


def chart_student_monthly(att, title):
    report = monthly_report(att)
    if report.empty:
        return None
    long_df = report.melt(
        id_vars="Month", value_vars=STATUSES, var_name="Status", value_name="Days"
    )
    fig = px.bar(
        long_df, x="Month", y="Days", color="Status", barmode="stack", title=title,
        color_discrete_map=STATUS_COLORS,
    )
    fig.update_xaxes(type="category")
    return style_figure(fig)


def show_chart(fig, empty_message="Not enough data to draw this chart yet."):
    if fig is None:
        st.info(empty_message)
    else:
        st.plotly_chart(fig)


# ---------------------------------------------------------------------------
# Import / export helpers
# ---------------------------------------------------------------------------
def _safe_cell(value):
    """Prevent spreadsheet formula injection in exported text cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def sanitize_for_export(df):
    out = df.copy()
    for column in out.columns:
        if not pd.api.types.is_numeric_dtype(out[column]) and not pd.api.types.is_datetime64_any_dtype(
            out[column]
        ):
            out[column] = out[column].map(_safe_cell)
    return out


def to_csv_bytes(df):
    return sanitize_for_export(df).to_csv(index=False).encode("utf-8")


def to_excel_bytes(sheets):
    """Write {sheet_name: DataFrame} into an in-memory .xlsx file."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, df in sheets.items():
            sanitize_for_export(df).to_excel(writer, sheet_name=name[:31], index=False)
    return buffer.getvalue()


def export_records_frame(att):
    if att.empty:
        return pd.DataFrame(columns=["Student ID", "Name", "Class", "Date", "Status"])
    out = att[["Student ID", "Name", "Class", "Date", "Status"]].copy()
    out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
    return out


def normalise_column_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def read_upload(uploaded_file):
    """Read a CSV / Excel upload. Returns (DataFrame or None, error message or None)."""
    size_mb = (uploaded_file.size or 0) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        return None, f"The file is {size_mb:.1f} MB. The maximum allowed size is {MAX_UPLOAD_MB} MB."
    filename = uploaded_file.name.lower()
    try:
        uploaded_file.seek(0)
        if filename.endswith(".csv"):
            df = pd.read_csv(uploaded_file, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        elif filename.endswith(".xlsx"):
            df = pd.read_excel(uploaded_file, dtype=str, engine="openpyxl")
        else:
            return None, "Unsupported file type. Please upload a .csv or .xlsx file."
    except pd.errors.EmptyDataError:
        return None, "The file is empty. Please upload a file that contains a header row and data."
    except UnicodeDecodeError:
        return None, "The CSV file could not be read. Please save it as 'CSV UTF-8' and try again."
    except pd.errors.ParserError as exc:
        return None, f"The file is not a valid CSV file: {exc}"
    except Exception as exc:  # noqa: BLE001 - corrupt Excel files, etc.
        return None, f"The file could not be read. Make sure it is a valid .csv or .xlsx file. ({exc})"
    df = df.fillna("").astype(str)
    blank_rows = (df.apply(lambda column: column.str.strip()) == "").all(axis=1)
    df = df[~blank_rows].reset_index(drop=True)
    if df.empty or len(df.columns) == 0:
        return None, "The file has no data rows."
    return df, None


def standardise_columns(df):
    """Rename columns to student_id / name / class / date / status."""
    lookup = {}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            lookup[alias] = target
    renamed = {}
    for column in df.columns:
        target = lookup.get(normalise_column_name(column))
        if target and target not in renamed.values():
            renamed[column] = target
    df = df.rename(columns=renamed)
    missing = [c for c in ("student_id", "date", "status") if c not in df.columns]
    if missing:
        pretty = {"student_id": "Student ID", "date": "Date", "status": "Status"}
        return None, (
            "Missing required column(s): "
            + ", ".join(pretty[m] for m in missing)
            + ". Required columns are: Student ID, Date, Status "
            "(plus Name and Class for students that are not registered yet)."
        )
    for optional in ("name", "class"):
        if optional not in df.columns:
            df[optional] = ""
    return df[["student_id", "name", "class", "date", "status"]], None


def parse_date_value(value):
    """Parse many date formats; returns a datetime.date or None."""
    text = str(value).strip()
    if not text:
        return None
    try:
        return date_parser.parse(text).date()
    except (ValueError, OverflowError, TypeError):
        return None


def validate_upload(df, students):
    """Validate each row. Returns (clean_df, errors_df)."""
    known = {str(sid).lower() for sid in students["Student ID"]} if not students.empty else set()
    clean_rows, errors = [], []
    seen = set()
    today = date.today()
    for position, row in enumerate(df.itertuples(index=False), start=2):  # row 1 = header
        sid = str(row.student_id).strip()
        name = " ".join(str(row.name).split())
        class_name = " ".join(str(row[2]).split())
        problems = []
        if not sid:
            problems.append("Student ID is empty")
        elif not ID_PATTERN.match(sid):
            problems.append(f"Invalid Student ID '{sid}'")
        parsed_date = parse_date_value(row.date)
        if parsed_date is None:
            problems.append(f"Invalid date '{row.date}'")
        elif parsed_date > today:
            problems.append(f"Date {parsed_date.isoformat()} is in the future")
        status = STATUS_ALIASES.get(str(row.status).strip().lower())
        if status is None:
            problems.append(f"Invalid status '{row.status}' (use Present, Absent, or Late)")
        if sid and ID_PATTERN.match(sid) and sid.lower() not in known:
            if not name or not class_name:
                problems.append(f"Student '{sid}' is not registered, so Name and Class are required")
        if not problems:
            key = (sid.lower(), parsed_date)
            if key in seen:
                problems.append("Duplicate entry for the same student and date in this file")
            else:
                seen.add(key)
        if problems:
            errors.append({"Row": position, "Problem": "; ".join(problems)})
        else:
            clean_rows.append(
                {
                    "student_id": sid,
                    "name": name,
                    "class": class_name,
                    "date": parsed_date.isoformat(),
                    "status": status,
                }
            )
    clean_df = pd.DataFrame(clean_rows, columns=["student_id", "name", "class", "date", "status"])
    return clean_df, pd.DataFrame(errors, columns=["Row", "Problem"])


def import_rows(clean_df):
    """Write validated rows to the database. Returns (success, message)."""
    new_students = inserted = updated = 0
    try:
        with db_connection() as conn:
            for row in clean_df.itertuples(index=False):
                found = conn.execute(
                    "SELECT id FROM students WHERE student_id = ?", (row.student_id,)
                ).fetchone()
                if found is None:
                    stored_class = ensure_class(conn, row[2])
                    cursor = conn.execute(
                        "INSERT INTO students (student_id, name, class_name) VALUES (?, ?, ?)",
                        (row.student_id, row.name, stored_class),
                    )
                    pk = cursor.lastrowid
                    new_students += 1
                else:
                    pk = found["id"]
                exists = conn.execute(
                    "SELECT 1 FROM attendance WHERE student_pk = ? AND attendance_date = ?",
                    (pk, row.date),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO attendance (student_pk, attendance_date, status)
                    VALUES (?, ?, ?)
                    ON CONFLICT (student_pk, attendance_date)
                    DO UPDATE SET status = excluded.status
                    """,
                    (pk, row.date, row.status),
                )
                if exists:
                    updated += 1
                else:
                    inserted += 1
        return True, (
            f"Import finished: {inserted} new attendance record(s), {updated} updated, "
            f"{new_students} new student(s) created."
        )
    except sqlite3.Error as exc:
        return False, f"Database error during import. Nothing was saved. Details: {exc}"


TEMPLATE_CSV = (
    "Student ID,Name,Class,Date,Status\n"
    "S001,Ayesha Khan,10-A,2026-09-01,Present\n"
    "S002,Bilal Ahmed,10-A,2026-09-01,Absent\n"
    "S003,Sara Ali,10-B,2026-09-01,Late\n"
).encode("utf-8")


# ---------------------------------------------------------------------------
# Gemini AI helpers
# ---------------------------------------------------------------------------
def get_gemini_key():
    """Read GEMINI_API_KEY from Streamlit secrets first, then environment variables."""
    key = None
    try:
        key = st.secrets.get("GEMINI_API_KEY")
    except Exception:  # noqa: BLE001 - no secrets file is a normal situation
        key = None
    if not key:
        key = os.environ.get("GEMINI_API_KEY")
    return str(key).strip() if key else None


def get_gemini_model():
    model = None
    try:
        model = st.secrets.get("GEMINI_MODEL")
    except Exception:  # noqa: BLE001
        model = None
    return str(model or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL)


AI_SYSTEM_INSTRUCTION = (
    "You are an attendance analysis assistant for a school or college. "
    "Use ONLY the attendance data supplied in the prompt. Never invent students, "
    "numbers, dates, or statistics. If the data does not contain the answer, say so "
    "clearly. Be concise, well organised, and practical. Attendance % counts "
    "Present and Late as attended. Suggestions must be general and supportive, "
    "not accusatory toward students."
)


def call_gemini(prompt):
    """Send a prompt to Gemini. Returns (success, text_or_error_message)."""
    api_key = get_gemini_key()
    if not api_key:
        return False, (
            "No Gemini API key was found. Add GEMINI_API_KEY to .streamlit/secrets.toml "
            "(or Streamlit Cloud Secrets), or set it as an environment variable. "
            "See the README for step-by-step instructions."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return False, "The 'google-genai' package is not installed. Run: pip install -r requirements.txt"
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=get_gemini_model(),
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=AI_SYSTEM_INSTRUCTION, temperature=0.3
            ),
        )
        text = getattr(response, "text", None)
        if not text:
            return False, "Gemini returned an empty answer (it may have been blocked). Try rephrasing."
        return True, text
    except Exception as exc:  # noqa: BLE001 - network, quota, invalid key, etc.
        message = str(exc).replace(api_key, "***")
        return False, f"The Gemini request failed: {message}"


def build_ai_context(att, students, threshold, class_name, include_names):
    """Turn real database data into a compact text block for Gemini."""
    if att.empty:
        return None
    data = att if class_name == "All classes" else att[att["Class"] == class_name]
    roster = students if class_name == "All classes" else students[students["Class"] == class_name]
    if data.empty:
        return None
    summary = student_summary(roster, data, threshold)
    low = summary[summary["Low Attendance"]].sort_values("Attendance %").head(MAX_AI_ROWS)
    low_cols = ["Student ID", "Name", "Class"] if include_names else ["Student ID", "Class"]
    low = low[low_cols + ["Present", "Late", "Absent", "Total Days", "Attendance %"]]
    daily = daily_report(data).head(MAX_AI_ROWS)
    overall = overall_percentage(data)
    parts = [
        f"SCOPE: {class_name}",
        f"DATE RANGE: {data['Date'].min().date()} to {data['Date'].max().date()}",
        f"TOTAL STUDENTS REGISTERED: {len(roster)}",
        f"TOTAL ATTENDANCE RECORDS: {len(data)}",
        f"OVERALL ATTENDANCE %: {overall} (Present + Late counted as attended)",
        f"LOW-ATTENDANCE THRESHOLD: below {threshold}%",
        f"STUDENTS BELOW THRESHOLD: {int(summary['Low Attendance'].sum())}",
        "",
        "CLASS-WISE REPORT (CSV):",
        class_report(data).to_csv(index=False),
        "MONTHLY REPORT (CSV):",
        monthly_report(data).to_csv(index=False),
        f"MOST RECENT DAILY REPORT, up to {MAX_AI_ROWS} days (CSV):",
        daily.to_csv(index=False),
        f"LOWEST-ATTENDANCE STUDENTS, up to {MAX_AI_ROWS} (CSV):",
        low.to_csv(index=False),
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
.metric-card {
    background: rgba(128, 128, 128, 0.08);
    border: 1px solid rgba(128, 128, 128, 0.2);
    border-left: 6px solid var(--accent, #3B5BDB);
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 12px;
}
.metric-card .label { font-size: 0.85rem; opacity: 0.75; margin-bottom: 4px; }
.metric-card .value { font-size: 1.9rem; font-weight: 700; line-height: 1.2; }
.metric-card .sub { font-size: 0.78rem; opacity: 0.65; margin-top: 4px; }
</style>
"""


def metric_card(label, value, sub="", accent=PRIMARY_COLOR):
    st.markdown(
        f"""
        <div class="metric-card" style="--accent: {accent};">
            <div class="label">{label}</div>
            <div class="value">{value}</div>
            <div class="sub">{sub}&nbsp;</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def flash(kind, message):
    """Queue a message that is displayed after the next rerun."""
    st.session_state["flash"].append((kind, message))


def show_flash():
    messages = st.session_state.get("flash", [])
    for kind, message in messages:
        if kind == "success":
            st.success(message)
        elif kind == "error":
            st.error(message)
        elif kind == "warning":
            st.warning(message)
        else:
            st.info(message)
    st.session_state["flash"] = []


def format_percent(value):
    return "N/A" if value is None or pd.isna(value) else f"{value:.1f}%"


def display_table(df):
    """Show a clean table (hides the internal 'pk' column and the index)."""
    view = df.drop(columns=[c for c in ["pk"] if c in df.columns])
    st.dataframe(view, hide_index=True)


def student_label(row):
    return f"{row['Student ID']} — {row['Name']} ({row['Class']})"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def page_dashboard(students, att, threshold):
    st.title("📊 Dashboard")
    st.caption("A quick overview of your attendance data.")
    summary = student_summary(students, att, threshold)
    class_days = att["Date"].dt.date.nunique() if not att.empty else 0
    present = int(att["Status"].isin(ATTENDED_STATUSES).sum()) if not att.empty else 0
    absent = int((att["Status"] == "Absent").sum()) if not att.empty else 0
    low_count = int(summary["Low Attendance"].sum()) if not summary.empty else 0
    classes = students["Class"].nunique() if not students.empty else 0

    c1, c2, c3 = st.columns(3)
    with c1:
        metric_card("Total Students", len(students), "Registered students", PRIMARY_COLOR)
    with c2:
        metric_card("Total Classes", classes, f"{class_days} class day(s) recorded", "#7048E8")
    with c3:
        metric_card("Present Students", present, "Present + Late records", STATUS_COLORS["Present"])
    c4, c5, c6 = st.columns(3)
    with c4:
        metric_card("Absent Students", absent, "Absent records", STATUS_COLORS["Absent"])
    with c5:
        metric_card("Overall Attendance", format_percent(overall_percentage(att)), "Present + Late", "#1098AD")
    with c6:
        metric_card("Low Attendance Students", low_count, f"Below {threshold}%", STATUS_COLORS["Late"])

    if students.empty:
        st.info("Welcome! Start by adding students in the **Students** page, then mark attendance.")
        return
    if att.empty:
        st.info("No attendance has been recorded yet. Use **Mark Attendance** or **Import / Export**.")
        return

    left, right = st.columns(2)
    with left:
        show_chart(chart_status_split(att))
    with right:
        show_chart(chart_class_comparison(att))
    show_chart(chart_monthly_trend(att))

    st.subheader("⚠️ Students with low attendance")
    low = summary[summary["Low Attendance"]].sort_values("Attendance %")
    if low.empty:
        st.success(f"No students are below the {threshold}% attendance threshold.")
    else:
        display_table(low.drop(columns=["Low Attendance"]))


def page_students(students):
    st.title("🎓 Student Management")
    tab_add, tab_view, tab_edit = st.tabs(["➕ Add Student", "👀 View Students", "✏️ Edit / Delete"])

    with tab_add:
        with st.form("add_student_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            student_id = c1.text_input("Student ID / Roll number", max_chars=30)
            name = c2.text_input("Student name", max_chars=100)
            class_name = c3.text_input("Class / Section", max_chars=50, help="Example: 10-A")
            submitted = st.form_submit_button("Add student", type="primary")
        if submitted:
            ok, message = add_student(student_id, name, class_name)
            flash("success" if ok else "error", message)
            if ok:
                st.rerun()
            else:
                show_flash()

    with tab_view:
        if students.empty:
            st.info("No students registered yet.")
        else:
            c1, c2 = st.columns([2, 1])
            query = c1.text_input("🔍 Search by name or student ID", key="student_search").strip()
            class_options = ["All classes"] + sorted(students["Class"].unique())
            class_filter = c2.selectbox("Class", class_options, key="student_class_filter")
            view = students
            if query:
                mask = students["Name"].str.contains(query, case=False, regex=False) | students[
                    "Student ID"
                ].str.contains(query, case=False, regex=False)
                view = view[mask]
            if class_filter != "All classes":
                view = view[view["Class"] == class_filter]
            st.caption(f"Showing {len(view)} of {len(students)} students")
            display_table(view)

    with tab_edit:
        if students.empty:
            st.info("Add a student first.")
        else:
            labels = {int(r["pk"]): student_label(r) for _, r in students.iterrows()}
            pk = st.selectbox(
                "Select a student", options=list(labels.keys()), format_func=lambda k: labels[k]
            )
            row = students[students["pk"] == pk].iloc[0]
            with st.form(f"edit_form_{pk}"):
                c1, c2, c3 = st.columns(3)
                new_id = c1.text_input("Student ID / Roll number", value=row["Student ID"], max_chars=30)
                new_name = c2.text_input("Student name", value=row["Name"], max_chars=100)
                new_class = c3.text_input("Class / Section", value=row["Class"], max_chars=50)
                update_clicked = st.form_submit_button("Save changes", type="primary")
            if update_clicked:
                ok, message = update_student(pk, new_id, new_name, new_class)
                flash("success" if ok else "error", message)
                if ok:
                    st.rerun()
                else:
                    show_flash()
            st.divider()
            st.markdown("**Danger zone**")
            confirm = st.checkbox(
                "I understand this permanently deletes the student and all their attendance records.",
                key=f"confirm_delete_{pk}",
            )
            if st.button("🗑️ Delete student", disabled=not confirm, key=f"delete_{pk}"):
                ok, message = delete_student(pk)
                flash("success" if ok else "error", message)
                st.rerun()


def page_mark_attendance(students, att):
    st.title("📝 Mark Attendance")
    if students.empty:
        st.info("Add students first in the **Students** page.")
        return
    classes = sorted(students["Class"].unique())
    c1, c2, c3 = st.columns(3)
    selected_date = c1.date_input("Date", value=date.today(), max_value=date.today())
    selected_class = c2.selectbox("Class / Section", classes)
    default_status = c3.selectbox("Default for students not yet marked", STATUSES, index=0)
    if not isinstance(selected_date, date):
        st.warning("Please select a valid date.")
        return
    date_str = selected_date.isoformat()

    roster = students[students["Class"] == selected_class].reset_index(drop=True)
    existing = {}
    if not att.empty:
        mask = (att["Date"].dt.strftime("%Y-%m-%d") == date_str) & (att["Class"] == selected_class)
        existing = dict(zip(att.loc[mask, "pk"].astype(int), att.loc[mask, "Status"]))
    if existing:
        st.info(
            f"Attendance for **{selected_class}** on **{date_str}** already exists. "
            "Changing the statuses below and saving will update it."
        )

    editor_df = pd.DataFrame(
        {
            "pk": roster["pk"].astype(int),
            "Student ID": roster["Student ID"],
            "Name": roster["Name"],
            "Status": [existing.get(int(pk), default_status) for pk in roster["pk"]],
        }
    )
    editor_key = f"att_editor_{date_str}_{selected_class}_{default_status}_{st.session_state['att_version']}"
    edited = st.data_editor(
        editor_df,
        hide_index=True,
        disabled=["pk", "Student ID", "Name"],
        column_config={
            "pk": None,
            "Status": st.column_config.SelectboxColumn("Status", options=STATUSES, required=True),
        },
        key=editor_key,
    )
    counts = edited["Status"].value_counts()
    st.caption(
        f"Present: {int(counts.get('Present', 0))} · Late: {int(counts.get('Late', 0))} · "
        f"Absent: {int(counts.get('Absent', 0))}"
    )

    col_save, col_delete, _ = st.columns([1, 1, 3])
    if col_save.button("💾 Save attendance", type="primary"):
        records = list(zip(edited["pk"].astype(int), edited["Status"]))
        ok, message = save_attendance(date_str, records)
        flash("success" if ok else "error", message)
        if ok:
            st.session_state["att_version"] += 1
        st.rerun()
    if existing and col_delete.button("🗑️ Delete this day"):
        ok, message = delete_attendance_for_day(date_str, selected_class)
        flash("success" if ok else "error", message)
        st.session_state["att_version"] += 1
        st.rerun()


def apply_record_filters(att, key_prefix):
    """Search and filter widgets shared by the Records page."""
    c1, c2, c3 = st.columns(3)
    name_query = c1.text_input("🔍 Student name", key=f"{key_prefix}_name").strip()
    id_query = c2.text_input("🔍 Student ID", key=f"{key_prefix}_id").strip()
    class_filter = c3.selectbox(
        "Class", ["All classes"] + sorted(att["Class"].unique()), key=f"{key_prefix}_class"
    )
    c4, c5 = st.columns(2)
    status_filter = c4.multiselect("Status", STATUSES, default=STATUSES, key=f"{key_prefix}_status")
    use_dates = c5.checkbox("Filter by date", key=f"{key_prefix}_use_dates")
    result = att
    if name_query:
        result = result[result["Name"].str.contains(name_query, case=False, regex=False)]
    if id_query:
        result = result[result["Student ID"].str.contains(id_query, case=False, regex=False)]
    if class_filter != "All classes":
        result = result[result["Class"] == class_filter]
    result = result[result["Status"].isin(status_filter)]
    if use_dates:
        min_date, max_date = att["Date"].min().date(), att["Date"].max().date()
        picked = c5.date_input(
            "Date or date range", value=(min_date, max_date),
            min_value=min_date, max_value=max_date, key=f"{key_prefix}_dates",
        )
        if isinstance(picked, (tuple, list)):
            if len(picked) == 2:
                start, end = picked
            elif len(picked) == 1:
                start = end = picked[0]
            else:
                start, end = min_date, max_date
        else:
            start = end = picked
        result = result[(result["Date"].dt.date >= start) & (result["Date"].dt.date <= end)]
    return result


def page_records(att):
    st.title("🗂️ Attendance Records")
    if att.empty:
        st.info("No attendance records yet.")
        return
    filtered = apply_record_filters(att, "rec")
    st.caption(f"Showing {len(filtered)} of {len(att)} records. To change a record, use **Mark Attendance**.")
    view = filtered.copy()
    view["Date"] = view["Date"].dt.strftime("%Y-%m-%d")
    display_table(view.drop(columns=["Month"]))
    if not filtered.empty:
        st.download_button(
            "⬇️ Download these records (CSV)",
            data=to_csv_bytes(export_records_frame(filtered)),
            file_name="attendance_records_filtered.csv",
            mime="text/csv",
        )


def page_analysis(students, att, threshold):
    st.title("📈 Analysis & Charts")
    if att.empty:
        st.info("No attendance data to analyse yet.")
        return
    class_filter = st.selectbox("Class", ["All classes"] + sorted(students["Class"].unique()))
    data = att if class_filter == "All classes" else att[att["Class"] == class_filter]
    roster = students if class_filter == "All classes" else students[students["Class"] == class_filter]
    if data.empty:
        st.info("This class has no attendance records yet.")
        return

    tab_student, tab_class, tab_daily, tab_monthly, tab_charts = st.tabs(
        ["🎓 Student report", "🏫 Class report", "📅 Daily report", "🗓️ Monthly report", "📊 Charts"]
    )
    with tab_student:
        summary = student_summary(roster, data, threshold)
        st.caption(f"Students below {threshold}% are marked as low attendance.")
        display_table(summary)
        st.download_button("⬇️ CSV", to_csv_bytes(summary), "student_summary.csv", "text/csv", key="dl_student")
        low = summary[summary["Low Attendance"]]
        st.subheader("Students with low attendance")
        if low.empty:
            st.success("No students are below the threshold.")
        else:
            display_table(low.sort_values("Attendance %"))
    with tab_class:
        report = class_report(data)
        display_table(report)
        st.download_button("⬇️ CSV", to_csv_bytes(report), "class_report.csv", "text/csv", key="dl_class")
    with tab_daily:
        report = daily_report(data)
        display_table(report)
        st.download_button("⬇️ CSV", to_csv_bytes(report), "daily_report.csv", "text/csv", key="dl_daily")
    with tab_monthly:
        report = monthly_report(data)
        display_table(report)
        st.download_button("⬇️ CSV", to_csv_bytes(report), "monthly_report.csv", "text/csv", key="dl_monthly")
    with tab_charts:
        c1, c2 = st.columns(2)
        with c1:
            show_chart(chart_status_split(data))
        with c2:
            show_chart(chart_class_comparison(data))
        show_chart(chart_monthly_trend(data))
        st.subheader("Individual student chart")
        if roster.empty:
            st.info("No students available.")
        else:
            labels = {int(r["pk"]): student_label(r) for _, r in roster.iterrows()}
            pk = st.selectbox(
                "Student", list(labels.keys()), format_func=lambda k: labels[k], key="chart_student"
            )
            student_data = data[data["pk"] == pk]
            if student_data.empty:
                st.info("This student has no attendance records yet.")
            else:
                s1, s2 = st.columns(2)
                with s1:
                    show_chart(chart_status_split(student_data, "Attendance status"))
                with s2:
                    show_chart(chart_student_monthly(student_data, "Days per month"))


def render_import_tab(students):
    st.markdown(
        "Upload a **CSV** or **Excel (.xlsx)** file with these columns:\n\n"
        "- **Student ID** (required)  \n"
        "- **Date** (required, best format `YYYY-MM-DD`)  \n"
        "- **Status** (required: Present, Absent, or Late)  \n"
        "- **Name** and **Class** (required only for students not registered yet)"
    )
    st.download_button(
        "📄 Download sample CSV template", TEMPLATE_CSV, "attendance_template.csv", "text/csv"
    )
    uploaded = st.file_uploader(
        "Choose a file", type=["csv", "xlsx"], key=f"upload_file_{st.session_state['upload_version']}"
    )
    if uploaded is None:
        return
    raw, error = read_upload(uploaded)
    if error:
        st.error(error)
        return
    standard, error = standardise_columns(raw)
    if error:
        st.error(error)
        return
    clean, errors = validate_upload(standard, students)
    st.write(
        f"**{len(standard)}** row(s) found · **{len(clean)}** valid · **{len(errors)}** with problems"
    )
    if not errors.empty:
        st.warning("Some rows have problems and will be skipped:")
        st.dataframe(errors, hide_index=True)
    if clean.empty:
        st.error("There are no valid rows to import.")
        return
    st.markdown("**Preview of valid rows**")
    st.dataframe(clean.head(50), hide_index=True)
    st.caption("Existing attendance for the same student and date will be updated, not duplicated.")
    if st.button("✅ Import valid rows", type="primary"):
        ok, message = import_rows(clean)
        flash("success" if ok else "error", message)
        if ok:
            st.session_state["upload_version"] += 1
        st.rerun()


def render_export_tab(students, att, threshold):
    if att.empty and students.empty:
        st.info("There is no data to export yet.")
        return
    student_list = (
        students[["Student ID", "Name", "Class"]]
        if not students.empty
        else pd.DataFrame(columns=["Student ID", "Name", "Class"])
    )
    sheets = {
        "Records": export_records_frame(att),
        "Student Summary": student_summary(students, att, threshold),
        "Class Report": class_report(att),
        "Daily Report": daily_report(att),
        "Monthly Report": monthly_report(att),
        "Students": student_list,
    }
    csv_files = [
        ("Attendance records", "Records", "attendance_records.csv"),
        ("Student summary", "Student Summary", "student_summary.csv"),
        ("Class report", "Class Report", "class_report.csv"),
        ("Daily report", "Daily Report", "daily_report.csv"),
        ("Monthly report", "Monthly Report", "monthly_report.csv"),
        ("Students list", "Students", "students.csv"),
    ]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**CSV downloads**")
        for label, key, file_name in csv_files:
            st.download_button(
                f"⬇️ {label}", to_csv_bytes(sheets[key]), file_name, "text/csv", key=f"csv_{key}"
            )
    with c2:
        st.markdown("**Excel download (all reports in one workbook)**")
        try:
            st.download_button(
                "⬇️ Download Excel workbook",
                to_excel_bytes(sheets),
                "attendance_reports.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not create the Excel file: {exc}")


def page_import_export(students, att, threshold):
    st.title("📁 Import / Export")
    tab_import, tab_export = st.tabs(["⬆️ Import attendance", "⬇️ Export reports"])
    with tab_import:
        render_import_tab(students)
    with tab_export:
        render_export_tab(students, att, threshold)


def page_ai_assistant(students, att, threshold):
    st.title("🤖 AI Attendance Assistant")
    st.caption(
        "Powered by Google Gemini. The AI only sees summaries calculated from your real "
        "attendance data, never made-up numbers."
    )
    if not get_gemini_key():
        st.warning(
            "Gemini API key not found. Add `GEMINI_API_KEY` to `.streamlit/secrets.toml` "
            "(or Streamlit Cloud → Settings → Secrets), then restart the app. The rest of "
            "the application works without it."
        )
    if att.empty:
        st.info("Record or import some attendance first. The AI needs real data to analyse.")
        return

    c1, c2 = st.columns([1, 1])
    class_choice = c1.selectbox("Scope", ["All classes"] + sorted(students["Class"].unique()))
    include_names = c2.checkbox(
        "Send student names to Gemini", value=False,
        help="When off, only student IDs are sent. Turn on only if you are allowed to share names.",
    )
    context = build_ai_context(att, students, threshold, class_choice, include_names)
    if context is None:
        st.info("No attendance data for this selection.")
        return

    tasks = {
        "📝 Attendance summary": "Write a clear summary of this attendance data: overall rate, "
        "best and weakest classes, and notable points.",
        "📉 Identify trends": "Identify attendance trends over time (daily and monthly) and mention "
        "anything unusual. Only use the numbers provided.",
        "⚠️ Explain low attendance": "Explain possible patterns behind the low attendance shown in the "
        "data (which classes, months, or students stand out). Only describe what the data shows and "
        "label any possible reasons as general possibilities, not facts.",
        "💡 Improvement ideas": "Suggest practical, general ways to improve attendance, based on the "
        "patterns in this data.",
    }
    st.subheader("Quick analysis")
    cols = st.columns(len(tasks))
    for col, (label, instruction) in zip(cols, tasks.items()):
        if col.button(label, key=f"task_{label}"):
            with st.spinner("Gemini is analysing your data..."):
                ok, text = call_gemini(f"{instruction}\n\nATTENDANCE DATA:\n{context}")
            st.session_state["ai_result"] = (label, ok, text)
    if st.session_state.get("ai_result"):
        label, ok, text = st.session_state["ai_result"]
        st.markdown(f"**{label}**")
        if ok:
            st.markdown(text)
        else:
            st.error(text)

    st.subheader("Ask a question about your data")
    for role, message in st.session_state["chat_history"]:
        with st.chat_message(role):
            st.markdown(message)
    question = st.chat_input("e.g. Which class had the lowest attendance last month?")
    if question:
        question = question.strip()[:1000]
        st.session_state["chat_history"].append(("user", question))
        history = "\n".join(
            f"{'User' if r == 'user' else 'Assistant'}: {m}"
            for r, m in st.session_state["chat_history"][-7:-1]
        )
        prompt = (
            f"ATTENDANCE DATA:\n{context}\n\nPREVIOUS CONVERSATION:\n{history or '(none)'}\n\n"
            f"QUESTION: {question}\n\nAnswer using only the attendance data above."
        )
        with st.spinner("Thinking..."):
            ok, text = call_gemini(prompt)
        reply = text if ok else f"⚠️ {text}"
        st.session_state["chat_history"].append(("assistant", reply))
        st.rerun()
    if st.session_state["chat_history"] and st.button("Clear conversation"):
        st.session_state["chat_history"] = []
        st.session_state["ai_result"] = None
        st.rerun()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def init_session_state():
    st.session_state.setdefault("flash", [])
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("ai_result", None)
    st.session_state.setdefault("att_version", 0)
    st.session_state.setdefault("upload_version", 0)
    st.session_state.setdefault("page", PAGES[0])
    st.session_state.setdefault("threshold", DEFAULT_THRESHOLD)


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="📊", layout="wide")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    init_session_state()
    try:
        init_db()
    except sqlite3.Error as exc:
        st.error(f"The database could not be created or opened: {exc}")
        st.stop()

    with st.sidebar:
        st.markdown(f"## 📊 {APP_TITLE}")
        page = st.radio("Navigation", PAGES, key="page")
        st.divider()
        threshold = st.slider(
            "Low attendance threshold (%)", min_value=10, max_value=100, step=5, key="threshold"
        )
        st.caption("Attendance % = (Present + Late) ÷ total recorded days.")

    students = load_students()
    att = load_attendance()
    show_flash()

    if page == PAGES[0]:
        page_dashboard(students, att, threshold)
    elif page == PAGES[1]:
        page_students(students)
    elif page == PAGES[2]:
        page_mark_attendance(students, att)
    elif page == PAGES[3]:
        page_records(att)
    elif page == PAGES[4]:
        page_analysis(students, att, threshold)
    elif page == PAGES[5]:
        page_import_export(students, att, threshold)
    else:
        page_ai_assistant(students, att, threshold)


if __name__ == "__main__":
    main()
