"""Configuration — everything secret comes from the environment, never code."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")

# Gemini model (free tier). gemini-2.5-flash is the current free-tier default.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ElevenLabs voice + models. Rachel (21m00Tcm4TlvDq8ikWAM) is ElevenLabs'
# stock calm female voice; override with any voice id from your account.
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "backend" / "clinic.db")))

VOICE_READY = bool(ELEVENLABS_API_KEY and GEMINI_API_KEY)
