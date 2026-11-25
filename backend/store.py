"""Clinic data store — SQLite, seeded with mock doctors and slots.

This is the single source of truth for availability, booking and
cancellation. The Pipecat tools, the REST API and the text demo mode all
call these functions, so a booking made by voice shows up on the
dashboard and vice versa.
"""
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .config import DB_PATH

DOCTORS = [
    # (name, speciality, fee_inr)
    ("Dr. Ananya Rao", "General Physician", 500),
    ("Dr. Vikram Reddy", "Cardiologist", 900),
    ("Dr. Meera Nair", "Dermatologist", 700),
    ("Dr. Kabir Shah", "Orthopaedic Surgeon", 800),
    ("Dr. Sana Sheikh", "Paediatrician", 600),
    ("Dr. Rohit Iyer", "ENT Specialist", 650),
]

# Slots offered each day. A couple of slots start pre-booked so the
# availability answers are not all identical.
SLOT_TIMES = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
              "14:00", "14:30", "15:00", "16:00", "16:30", "17:00"]
PREBOOKED = {("Dr. Vikram Reddy", 0, "09:00"), ("Dr. Ananya Rao", 0, "10:00")}

SCHEMA = """
CREATE TABLE IF NOT EXISTS doctors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    speciality TEXT NOT NULL,
    fee_inr INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id INTEGER NOT NULL REFERENCES doctors(id),
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',
    UNIQUE (doctor_id, date, time)
);
CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    doctor_id INTEGER NOT NULL REFERENCES doctors(id),
    slot_id INTEGER NOT NULL REFERENCES slots(id),
    phone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Create tables and seed mock data on first run (idempotent)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0] == 0:
            for name, speciality, fee in DOCTORS:
                conn.execute(
                    "INSERT INTO doctors (name, speciality, fee_inr) VALUES (?,?,?)",
                    (name, speciality, fee),
                )
            doctors = {r["name"]: r["id"] for r in conn.execute("SELECT * FROM doctors")}
            today = date.today()
            for offset in range(3):  # today, tomorrow, day after
                day = (today + timedelta(days=offset)).isoformat()
                for name, doctor_id in doctors.items():
                    for t in SLOT_TIMES:
                        status = "booked" if (name, offset, t) in PREBOOKED else "available"
                        conn.execute(
                            "INSERT OR IGNORE INTO slots (doctor_id, date, time, status)"
                            " VALUES (?,?,?,?)",
                            (doctor_id, day, t, status),
                        )
            # Two pre-existing bookings, so cancel-by-phone is demonstrable.
            _seed_appointment(conn, "9876543210", "Dr. Ananya Rao", 1, "09:30")
            _seed_appointment(conn, "9876543210", "Dr. Meera Nair", 0, "15:00")
        conn.commit()
    finally:
        conn.close()


def _seed_appointment(conn, phone, doctor_name, day_offset, time):
    day = (date.today() + timedelta(days=day_offset)).isoformat()
    row = conn.execute(
        "SELECT s.id, d.id AS doctor_id FROM slots s JOIN doctors d ON d.id=s.doctor_id"
        " WHERE d.name=? AND s.date=? AND s.time=?",
        (doctor_name, day, time),
    ).fetchone()
    if not row:
        return
    apt_id = _next_id(conn)
    conn.execute(
        "INSERT OR IGNORE INTO appointments (id, doctor_id, slot_id, phone) VALUES (?,?,?,?)",
        (apt_id, row["doctor_id"], row["id"], phone),
    )
    conn.execute("UPDATE slots SET status='booked' WHERE id=?", (row["id"],))


def _next_id(conn) -> str:
    n = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    return f"APT-{1001 + n}"


# ---------------------------------------------------------------- helpers

def normalize_phone(raw: str) -> str | None:
    """Spoken numbers arrive as 'nine eight ...' or '98765 43210'.

    Keep digits only, drop an India country code, and require exactly
    10 digits. Returns None when the number is not usable.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def spaced_phone(phone: str) -> str:
    """'9876543210' -> '9 8 7 6 5 4 3 2 1 0' for read-back on a call."""
    return " ".join(phone)


def _doctor_dict(row) -> dict:
    return {"id": row["id"], "name": row["name"],
            "speciality": row["speciality"], "fee_inr": row["fee_inr"]}


def list_doctors(db_path: Path = DB_PATH) -> list[dict]:
    conn = connect(db_path)
    try:
        return [_doctor_dict(r) for r in conn.execute("SELECT * FROM doctors ORDER BY name")]
    finally:
        conn.close()


def find_doctor(query: str, db_path: Path = DB_PATH) -> dict | None:
    """Match by name fragment ('Rao', 'Dr Ananya') or speciality ('cardio')."""
    if not query:
        return None
    q = query.strip().lower()
    conn = connect(db_path)
    try:
        for row in conn.execute("SELECT * FROM doctors"):
            if q in row["name"].lower() or q in row["speciality"].lower():
                return _doctor_dict(row)
        return None
    finally:
        conn.close()


def list_availability(doctor_id: int | None = None, doctor_name: str | None = None,
                      speciality: str | None = None, day: str | None = None,
                      db_path: Path = DB_PATH) -> list[dict]:
    """Available slots, optionally filtered. `day` is YYYY-MM-DD or 'today'/'tomorrow'."""
    if day in ("today", "tomorrow"):
        day = (date.today() + timedelta(days=0 if day == "today" else 1)).isoformat()
    conn = connect(db_path)
    try:
        sql = ("SELECT s.id AS slot_id, s.date, s.time, d.id AS doctor_id,"
               " d.name AS doctor_name, d.speciality, d.fee_inr FROM slots s"
               " JOIN doctors d ON d.id=s.doctor_id WHERE s.status='available'")
        args: list = []
        if doctor_id:
            sql += " AND d.id=?"; args.append(doctor_id)
        if doctor_name:
            sql += " AND d.name LIKE ?"; args.append(f"%{doctor_name}%")
        if speciality:
            sql += " AND d.speciality LIKE ?"; args.append(f"%{speciality}%")
        if day:
            sql += " AND s.date=?"; args.append(day)
        sql += " ORDER BY s.date, s.time, d.name"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _appointment_dict(row) -> dict:
    return {"id": row["id"], "doctor_id": row["doctor_id"],
            "doctor_name": row["doctor_name"], "speciality": row["speciality"],
            "date": row["date"], "time": row["time"],
            "phone": row["phone"], "status": row["status"]}


def list_appointments(phone: str | None = None, db_path: Path = DB_PATH) -> list[dict]:
    conn = connect(db_path)
    try:
        sql = ("SELECT a.id, a.doctor_id, d.name AS doctor_name, d.speciality,"
               " s.date, s.time, a.phone, a.status FROM appointments a"
               " JOIN doctors d ON d.id=a.doctor_id"
               " JOIN slots s ON s.id=a.slot_id")
        args: list = []
        if phone:
            sql += " WHERE a.phone=?"; args.append(phone)
        sql += " ORDER BY s.date, s.time"
        return [_appointment_dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def book_appointment(phone: str, slot_id: int, db_path: Path = DB_PATH) -> dict:
    """Book one slot. Atomic: the slot flips to booked only if still free."""
    phone = normalize_phone(phone)
    if not phone:
        return {"ok": False, "error": "I need a valid 10-digit phone number to book."}
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE slots SET status='booked' WHERE id=? AND status='available'", (slot_id,))
        if cur.rowcount == 0:
            return {"ok": False, "error": "That slot was just taken. Please pick another one."}
        row = conn.execute(
            "SELECT s.date, s.time, d.id AS doctor_id, d.name AS doctor_name,"
            " d.speciality FROM slots s JOIN doctors d ON d.id=s.doctor_id WHERE s.id=?",
            (slot_id,)).fetchone()
        apt_id = _next_id(conn)
        conn.execute(
            "INSERT INTO appointments (id, doctor_id, slot_id, phone) VALUES (?,?,?,?)",
            (apt_id, row["doctor_id"], slot_id, phone))
        conn.commit()
        return {"ok": True, "appointment_id": apt_id, "doctor_name": row["doctor_name"],
                "speciality": row["speciality"], "date": row["date"], "time": row["time"],
                "phone": phone}
    finally:
        conn.close()


def cancel_appointment(phone: str, appointment_id: str | None = None,
                       db_path: Path = DB_PATH) -> dict:
    """Cancel by phone. With no id, cancels the caller's next upcoming booking."""
    phone = normalize_phone(phone)
    if not phone:
        return {"ok": False, "error": "I need a valid 10-digit phone number to cancel."}
    conn = connect(db_path)
    try:
        if appointment_id:
            row = conn.execute(
                "SELECT a.id, a.slot_id, d.name AS doctor_name, s.date, s.time"
                " FROM appointments a JOIN doctors d ON d.id=a.doctor_id"
                " JOIN slots s ON s.id=a.slot_id"
                " WHERE a.id=? AND a.phone=? AND a.status='booked'",
                (appointment_id.strip().upper(), phone)).fetchone()
            if not row:
                return {"ok": False,
                        "error": "I could not find that booking under this phone number."}
        else:
            row = conn.execute(
                "SELECT a.id, a.slot_id, d.name AS doctor_name, s.date, s.time"
                " FROM appointments a JOIN doctors d ON d.id=a.doctor_id"
                " JOIN slots s ON s.id=a.slot_id"
                " WHERE a.phone=? AND a.status='booked' ORDER BY s.date, s.time LIMIT 1",
                (phone,)).fetchone()
            if not row:
                return {"ok": False,
                        "error": "There is no active booking under this phone number."}
        conn.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (row["id"],))
        conn.execute("UPDATE slots SET status='available' WHERE id=?", (row["slot_id"],))
        conn.commit()
        return {"ok": True, "appointment_id": row["id"],
                "doctor_name": row["doctor_name"], "date": row["date"], "time": row["time"]}
    finally:
        conn.close()
