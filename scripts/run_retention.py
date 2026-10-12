"""Run alert_events retention (rollup + prune) once.

Run from the bot root with the same venv:
  python scripts/run_retention.py --dry-run      # show what would happen
  python scripts/run_retention.py                # do it
  python scripts/run_retention.py --days 14      # override ALERT_EVENT_RETENTION_DAYS
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal, init_db
from app.retention.prune import run_retention


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print what would be rolled up/deleted; change nothing")
    ap.add_argument("--days", type=int, default=None, help="retention days (default: settings)")
    ap.add_argument("--batch-size", type=int, default=1000)
    args = ap.parse_args()

    init_db()  # make sure alert_daily_rollups exists (no-op if it does)
    session = SessionLocal()
    try:
        res = run_retention(session, retention_days=args.days, batch_size=args.batch_size, dry_run=args.dry_run)
        print(("[DRY RUN] " if args.dry_run else "") + res.summary())
        if res.buckets:
            print(f"{'day':<12}{'vertical':<20}{'channel':<16}{'prio':<6}{'severity':<12}count")
            for (day, vertical, channel, prio, sev), n in sorted(res.buckets.items()):
                print(f"{day.isoformat():<12}{vertical or '-':<20}{channel or '-':<16}{prio:<6}{sev or '-':<12}{n}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
