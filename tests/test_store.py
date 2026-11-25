"""Store tests — booking rules that the voice tools rely on."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import store


def test_phone_normalisation():
    assert store.normalize_phone("98765 43210") == "9876543210"
    assert store.normalize_phone("+91 98765-43210") == "9876543210"
    assert store.normalize_phone("09876543210") == "9876543210"
    assert store.normalize_phone("12345") is None
    assert store.normalize_phone("hello") is None


def test_booking_twice_fails(tmp_path):
    db = tmp_path / "test.db"
    store.init_db(db)
    slots = store.list_availability(db_path=db)
    assert slots, "seeded slots expected"
    slot_id = slots[0]["slot_id"]
    first = store.book_appointment("9876543210", slot_id, db_path=db)
    assert first["ok"] and first["appointment_id"].startswith("APT-")
    second = store.book_appointment("9123456780", slot_id, db_path=db)
    assert not second["ok"], "same slot must not book twice"


def test_cancel_frees_slot(tmp_path):
    db = tmp_path / "test.db"
    store.init_db(db)
    slots = store.list_availability(db_path=db)
    slot_id = slots[0]["slot_id"]
    booked = store.book_appointment("9876543210", slot_id, db_path=db)
    cancelled = store.cancel_appointment("9876543210", booked["appointment_id"], db_path=db)
    assert cancelled["ok"]
    again = store.book_appointment("9123456780", slot_id, db_path=db)
    assert again["ok"], "cancelled slot must be bookable again"


def test_cancel_wrong_phone_fails(tmp_path):
    db = tmp_path / "test.db"
    store.init_db(db)
    # seeded booking under 9876543210
    mine = store.list_appointments("9876543210", db_path=db)
    assert mine
    result = store.cancel_appointment("9999999999", mine[0]["id"], db_path=db)
    assert not result["ok"]
