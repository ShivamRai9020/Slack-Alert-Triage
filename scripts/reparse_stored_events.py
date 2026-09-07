"""Re-parse AlertEvent.raw_message into priority/service/vertical/etc.

Run from the bot root with the same venv:
  python scripts/reparse_stored_events.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import AlertEvent, AlertFingerprint
from app.parsing.parser import parse_alert_message


def main() -> None:
    session = SessionLocal()
    updated = 0
    try:
        events = session.execute(select(AlertEvent).order_by(AlertEvent.id)).scalars().all()
        for event in events:
            parsed = parse_alert_message(event.source_channel, event.raw_message or "")
            changed = False
            for field in (
                "priority",
                "severity",
                "vertical",
                "service",
                "environment",
                "alert_name",
                "host_or_pod",
            ):
                new = getattr(parsed, field)
                old = getattr(event, field)
                # Fill blanks; also overwrite when new parse has a value and old was wrong/empty
                if new and new != old:
                    setattr(event, field, new)
                    changed = True
            if changed:
                updated += 1
                fp = session.get(AlertFingerprint, event.fingerprint_id)
                if fp:
                    if parsed.vertical and not fp.vertical:
                        fp.vertical = parsed.vertical
                    if parsed.service and not fp.service:
                        fp.service = parsed.service
                    if parsed.alert_name and not fp.alert_name:
                        fp.alert_name = parsed.alert_name
        session.commit()
        print(f"Updated {updated} of {len(events)} events")
    finally:
        session.close()


if __name__ == "__main__":
    main()
