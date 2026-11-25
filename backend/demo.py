"""Text demo mode — the same booking flow over typed messages.

This is NOT the voice pipeline. It exists so the dashboard booking flow
can be tried and recorded without ElevenLabs / Gemini keys, and so the
booking logic can be tested end to end. It reuses backend.store exactly,
so anything booked here appears on the dashboard like a voice booking.

The real call path is backend/agent.py (Pipecat + ElevenLabs + Gemini).
When voice keys are set, use Try Call; this text mode is only a fallback.
"""
import re
from datetime import date, timedelta

from . import events, store

_WORD_DIGITS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
                "oh": "0", "o": "0"}

_SESSIONS: dict[str, dict] = {}


def _digits_from_text(text: str) -> str:
    lowered = text.lower()
    for word, digit in _WORD_DIGITS.items():
        lowered = re.sub(rf"\b{word}\b", digit, lowered)
    return re.sub(r"\D", "", lowered)


def _find_date(text: str) -> str | None:
    t = text.lower()
    if "day after" in t:
        return (date.today() + timedelta(days=2)).isoformat()
    if "tomorrow" in t:
        return (date.today() + timedelta(days=1)).isoformat()
    if "today" in t:
        return date.today().isoformat()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    return m.group(0) if m else None


def _find_time(text: str) -> str | None:
    m = re.search(r"(\d{1,2})[:.](\d{2})\s*(am|pm)?", text.lower())
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), m.group(2), m.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def _find_doctor(text: str):
    for doc in store.list_doctors():
        last = doc["name"].split()[-1].lower()
        first = doc["name"].split()[1].lower()
        if last in text.lower() or first in text.lower():
            return doc
    for speciality in ("cardio", "derma", "skin", "ortho", "bone", "paediatric",
                       "pediatric", "child", "ent", "ear", "general", "physician"):
        doc = store.find_doctor(speciality)
        if doc and speciality in text.lower():
            return doc
    return None


def reply(session_id: str, text: str) -> dict:
    """One turn of the text demo. Returns {"reply": ..., "booking": ...|None}."""
    s = _SESSIONS.setdefault(session_id, {"phone": None, "pending_phone": None,
                                          "doctor": None, "day": None, "slots": []})
    msg = (text or "").strip()
    low = msg.lower()

    # --- confirm a phone number just read back
    if s["pending_phone"] and low in ("yes", "yeah", "yep", "correct", "that's right", "right"):
        s["phone"] = s["pending_phone"]
        s["pending_phone"] = None
        return {"reply": f"Thanks, noted {s['phone']}. Which doctor or speciality do you need — for example, General Physician, Cardiologist, or Dermatologist? And which day: today, tomorrow, or the day after?"}

    # --- a phone number in this message
    digits = _digits_from_text(msg)
    phone = store.normalize_phone(digits) if digits else None
    if phone and not s["phone"]:
        s["pending_phone"] = phone
        return {"reply": f"Just to confirm, is your number {' '.join(phone)}? Please say yes to confirm."}

    if not s["phone"]:
        return {"reply": "Hello, this is Maya from CityCare Clinic. Could you share your 10-digit phone number first? That is how I find your bookings."}

    # --- cancel flow
    if any(w in low for w in ("cancel", "delete my booking")):
        apt_id = re.search(r"APT-\d+", msg.upper())
        result = store.cancel_appointment(s["phone"], apt_id.group(0) if apt_id else None)
        if result.get("ok"):
            events.publish("appointment_cancelled", {
                "appointment": {"id": result["appointment_id"],
                                "doctor_name": result["doctor_name"],
                                "date": result["date"], "time": result["time"],
                                "status": "cancelled"}})
            return {"reply": f"Done. Your appointment with {result['doctor_name']} on {result['date']} at {result['time']} is cancelled. Booking ID {result['appointment_id']}."}
        mine = store.list_appointments(s["phone"])
        if mine:
            lines = ", ".join(f"{a['id']} with {a['doctor_name']} on {a['date']} at {a['time']}" for a in mine)
            return {"reply": f"{result['error']} Your bookings are: {lines}. Tell me the booking ID to cancel."}
        return {"reply": result["error"]}

    # --- doctor / day in this message
    doctor = _find_doctor(msg)
    if doctor:
        s["doctor"] = doctor
    day = _find_date(msg)
    if day:
        s["day"] = day

    # --- pick a time from the offered slots
    picked = _find_time(msg)
    if picked and s["slots"]:
        slot = next((x for x in s["slots"] if x["time"] == picked), None)
        if slot:
            result = store.book_appointment(s["phone"], slot["slot_id"])
            s["slots"] = []
            if result.get("ok"):
                events.publish("appointment_booked", {
                    "appointment": {"id": result["appointment_id"],
                                    "doctor_name": result["doctor_name"],
                                    "speciality": result["speciality"],
                                    "date": result["date"], "time": result["time"],
                                    "phone": result["phone"], "status": "booked"}})
                return {"reply": f"Booked. {result['doctor_name']} on {result['date']} at {result['time']}. Your booking ID is {result['appointment_id']}. Anything else — another booking, or a cancellation?",
                        "booking": result}
            return {"reply": result["error"]}
        return {"reply": "That time is not in the list I offered. Please pick one of the times above."}

    # --- check availability
    if s["doctor"]:
        slots = store.list_availability(doctor_id=s["doctor"]["id"], day=s["day"])
        s["slots"] = slots
        if not slots:
            return {"reply": f"No free slots for {s['doctor']['name']} on that day. Shall I check another day or another doctor?"}
        shown = ", ".join(f"{x['date']} at {x['time']}" for x in slots[:3])
        return {"reply": f"{s['doctor']['name']} ({s['doctor']['speciality']}, ₹{s['doctor']['fee_inr']}) is free at: {shown}. Which time works for you?"}

    return {"reply": "Which doctor or speciality do you need, and which day — today, tomorrow, or the day after?"}
