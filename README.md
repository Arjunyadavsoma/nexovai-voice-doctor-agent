# Voice AI Doctor Appointment Booking — NexovAI SDE Intern, Round 1

A voice agent that books and cancels doctor appointments over a phone-style
call, with a live dashboard. Built for CityCare Clinic, Hyderabad (mock data).

**Deadline:** Wednesday, 25 November 2026, 11:00 PM IST

## What it does

- **Voice call (Pipecat):** the caller speaks to Maya, the clinic's voice
  agent. She asks for the caller's phone number — that is the only
  identification, no login, no OTP, no caller ID — reads it back to confirm,
  checks a doctor's availability, books a slot, or cancels an existing
  booking, all in one conversation.
- **Dashboard:** doctors and their open slots, appointments that update live
  when a call books or cancels, and a phone-style call screen (ringing →
  connected, timer, mute, live transcript, waveform, booking card, ring and
  end-call sounds).
- **Text demo:** the same booking flow over typed messages, so the dashboard
  can be tried without API keys. It is clearly labelled in the UI and in
  `backend/demo.py` — it is not the voice path.

## Stack

| Part | Choice |
|---|---|
| Voice pipeline | Pipecat 1.17 (Python) |
| STT | ElevenLabs (`scribe_v2`) |
| TTS | ElevenLabs (`eleven_turbo_v2_5`, voice Sarah — a premade voice that works on the free tier; Rachel is a Voice Library voice and is blocked for free accounts) |
| LLM | Google Gemini `gemini-2.5-flash` (free tier) |
| VAD | Silero VAD, in the Pipecat pipeline |
| Transport | SmallWebRTC — browser WebRTC straight to this server, no Daily/telephone account |
| Backend | FastAPI + SQLite (mock clinic data) |
| Frontend | Plain HTML/CSS/JS, no build step |

## Run it

```bash
cp .env.example .env   # add ELEVENLABS_API_KEY and GEMINI_API_KEY
./run.sh               # or: pip install -r requirements.txt && uvicorn backend.app:app --port 8000
```

Open http://localhost:8000 and click **Try Call**. Allow microphone access.

Without keys the server still runs: the status pill reads “text demo”,
the phone screen works in text mode, and booking/cancel via the dashboard
works. A voice call needs both keys.

Keys are free:
- Gemini — https://aistudio.google.com/apikey
- ElevenLabs — https://elevenlabs.io/app/settings/api-keys

## How the pipeline works

```
caller mic → SmallWebRTC → Silero VAD → ElevenLabs STT
           → Gemini LLM (+ 3 tools) → ElevenLabs TTS
           → SmallWebRTC → caller speaker
```

The LLM can call three tools, registered in the Pipecat pipeline
(`backend/tools.py`):

- `check_doctor_availability(doctor_name?, speciality?, date?)`
- `book_appointment(phone, slot_id)` — phone already read back and confirmed
- `cancel_appointment(phone, appointment_id?)` — no id cancels the next upcoming booking

All three go through `backend/store.py` (SQLite), the same store the
dashboard reads, so a voice booking appears on the dashboard immediately
and a dashboard cancellation frees the slot for the next caller.

`backend/agent.py` builds one pipeline per WebRTC connection
(`POST /api/offer` in `backend/app.py`). A `DashboardBridge` processor
after STT and after TTS forwards the live transcript to the dashboard
over `/ws`.

## Project layout

```
backend/
  app.py      FastAPI: dashboard, REST, /ws events, /api/offer WebRTC signalling
  agent.py    Pipecat pipeline (STT → LLM → TTS, Silero VAD, tools)
  tools.py    The 3 function tools + the agent's system prompt
  store.py    SQLite doctors / slots / appointments + booking rules
  demo.py     Text demo mode (same store, same flow, typed)
  config.py   Env config; no secrets in code
frontend/
  index.html  Dashboard + phone call screen
  styles.css  Warm paper / ink / one teal — no generic gradient look
  app.js      REST + WebSocket + WebRTC client, waveform, sounds
docs/
  PIPELINE.md Pipeline description, in my own words
```

## Mock data

6 doctors (GP, Cardiology, Dermatology, Orthopaedic, Paediatric, ENT),
slots today / tomorrow / day after, 09:00–17:00. Two bookings are
pre-seeded under phone `9876543210` so the cancel flow can be tried
without booking first. The database is created on first run
(`backend/clinic.db`, gitignored); delete it to reseed.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Covers phone normalisation, availability, booking a slot twice (second
attempt must fail), and cancel freeing the slot.

## Notes

- No API keys are committed. `.env` is gitignored; see `.env.example`.
- The agent never asks for OTPs or payments, and never invents a doctor,
  slot or booking ID — everything comes from the tools.
