"""Pipecat function tools — the three actions the voice agent can take.

Each tool is a plain async function whose docstring + signature Pipecat
turns into a function schema for Gemini. The docstrings matter: they are
what the LLM reads to decide when and how to call the tool, so they say
exactly what each argument is.

All three go through backend.store, the same store the dashboard reads,
so voice bookings appear on the dashboard immediately.
"""
from pipecat.services.llm_service import FunctionCallParams

from . import events, store

SYSTEM_PROMPT = """You are Maya, the voice booking assistant for CityCare Clinic in Hyderabad.
You speak on a phone call, so keep every reply to one or two short spoken
sentences. No lists, no formatting, no emojis. Say numbers as words the
caller can follow, and read phone numbers back digit by digit.

How a call goes:
1. Greet briefly and ask for the caller's 10-digit phone number. This is
   the only way you identify them — there is no login and no caller ID.
2. Read the number back digit by digit and ask them to confirm it before
   you book or cancel anything. If they correct it, use the corrected
   number and read it back again.
3. Ask which doctor or speciality they need, and which day — today,
   tomorrow, or the day after. Use check_doctor_availability, then offer
   at most three slots.
4. When they pick a slot, confirm the doctor, date and time out loud,
   then call book_appointment. Read the booking ID back to them.
5. To cancel, find their bookings with cancel_appointment using the
   confirmed phone number. Confirm the doctor and time before cancelling.
6. If a slot is taken or a number is invalid, say so plainly and offer
   the next option. Never invent a doctor, a slot, a time or a booking ID.
7. If the caller asks something outside booking — fees, directions,
   medical advice — answer only from what the tools return, and say you
   can only help with appointments.

Never ask for an OTP, a payment, or any detail other than the phone
number, doctor and time."""


async def check_doctor_availability(
    params: FunctionCallParams,
    doctor_name: str | None = None,
    speciality: str | None = None,
    date: str | None = None,
):
    """Check which appointment slots are free.

    Args:
        doctor_name: Doctor's name or part of it, e.g. "Ananya Rao" or "Rao".
        speciality: Speciality to look for, e.g. "Cardiologist", "Dermatologist".
        date: Day to check, as "today", "tomorrow", or a date like "2026-11-25".
    """
    slots = store.list_availability(doctor_name=doctor_name,
                                    speciality=speciality, day=date)
    if not slots:
        await params.result_callback(
            {"available": False,
             "message": "No free slots match that. Offer another doctor or day."})
        return
    # The LLM only needs a short list to speak; the dashboard shows the rest.
    short = [{"slot_id": s["slot_id"], "doctor": s["doctor_name"],
              "speciality": s["speciality"], "date": s["date"], "time": s["time"],
              "fee_inr": s["fee_inr"]} for s in slots[:6]]
    await params.result_callback(
        {"available": True, "count": len(slots), "slots": short})


async def book_appointment(
    params: FunctionCallParams,
    phone: str,
    slot_id: int,
):
    """Book a slot for the caller.

    Args:
        phone: The caller's 10-digit phone number, already read back and confirmed.
        slot_id: The slot id returned by check_doctor_availability.
    """
    result = store.book_appointment(phone, slot_id)
    if result.get("ok"):
        events.publish("appointment_booked", {
            "appointment": {"id": result["appointment_id"],
                            "doctor_name": result["doctor_name"],
                            "speciality": result["speciality"],
                            "date": result["date"], "time": result["time"],
                            "phone": result["phone"], "status": "booked"}})
        await params.result_callback({
            "booked": True, "appointment_id": result["appointment_id"],
            "doctor": result["doctor_name"], "date": result["date"],
            "time": result["time"],
            "message": "Booking confirmed. Read the booking ID back to the caller."})
    else:
        await params.result_callback({"booked": False, "message": result["error"]})


async def cancel_appointment(
    params: FunctionCallParams,
    phone: str,
    appointment_id: str | None = None,
):
    """Cancel the caller's booking.

    Args:
        phone: The caller's 10-digit phone number, already read back and confirmed.
        appointment_id: Booking ID like "APT-1001", if the caller knows it.
            Leave empty to cancel their next upcoming booking.
    """
    result = store.cancel_appointment(phone, appointment_id)
    if result.get("ok"):
        events.publish("appointment_cancelled", {
            "appointment": {"id": result["appointment_id"],
                            "doctor_name": result["doctor_name"],
                            "date": result["date"], "time": result["time"],
                            "status": "cancelled"}})
        await params.result_callback({
            "cancelled": True, "appointment_id": result["appointment_id"],
            "doctor": result["doctor_name"], "date": result["date"],
            "time": result["time"],
            "message": "Cancellation confirmed. Read it back to the caller."})
    else:
        await params.result_callback({"cancelled": False, "message": result["error"]})


TOOLS = [check_doctor_availability, book_appointment, cancel_appointment]
