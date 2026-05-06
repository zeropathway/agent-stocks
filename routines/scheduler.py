"""
Local 24/7 scheduler — runs all three routines at their ET times.

The `schedule` library uses the machine's local wall clock, NOT timezones.
_et_hhmm_to_local() converts each ET target time to the machine's local time
at startup so routines fire at the correct moment regardless of where the
machine is located (Norway, US, anywhere).

Usage:
    python routines/scheduler.py

Ctrl-C or SIGTERM to stop cleanly.
"""

import logging
import signal
import sys
import time
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import schedule

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent.parent / "scheduler.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

_STOP = False


# ------------------------------------------------------------------
# Timezone conversion — ET → machine local time
# ------------------------------------------------------------------

def _et_hhmm_to_local(et_hhmm: str) -> str:
    """
    Convert a HH:MM clock time in US/Eastern to the machine's local HH:MM.
    Called once at startup so DST is handled correctly for today.
    The scheduler should be restarted on DST transition days (twice a year).
    """
    h, m = map(int, et_hhmm.split(":"))
    target_et = datetime.now(tz=ET).replace(hour=h, minute=m, second=0, microsecond=0)
    local = target_et.astimezone()
    local_hhmm = local.strftime("%H:%M")
    log.info("Scheduled %s ET → %s %s (local)", et_hhmm, local_hhmm, local.strftime("%Z"))
    return local_hhmm


# ------------------------------------------------------------------
# Market holiday list (US)
# ------------------------------------------------------------------

_HOLIDAYS_2026 = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 11, 27),  # Day after Thanksgiving
    date(2026, 12, 25),  # Christmas
}

_HOLIDAYS_2027 = {
    date(2027, 1, 1),    # New Year's Day
    date(2027, 1, 18),   # MLK Day
    date(2027, 2, 15),   # Presidents' Day
    date(2027, 3, 26),   # Good Friday
    date(2027, 5, 31),   # Memorial Day
    date(2027, 7, 5),    # Independence Day (observed)
    date(2027, 9, 6),    # Labor Day
    date(2027, 11, 25),  # Thanksgiving
    date(2027, 11, 26),  # Day after Thanksgiving
    date(2027, 12, 24),  # Christmas (observed)
}

_HOLIDAYS: set[date] = _HOLIDAYS_2026 | _HOLIDAYS_2027


def _is_trading_day() -> bool:
    now = datetime.now(tz=ET)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    if now.date() in _HOLIDAYS:
        return False
    return True


def _run_guarded(name: str, fn):
    """Run a routine, guarding against exceptions and non-trading days."""
    if not _is_trading_day():
        log.info("Skipping %s — not a trading day", name)
        return
    log.info("─── Starting %s routine ───", name)
    try:
        fn()
    except Exception as e:
        log.error("%s routine failed: %s", name, e, exc_info=True)
    log.info("─── %s routine complete ───", name)


def _premarket():
    from routines.premarket import run
    _run_guarded("premarket", run)


def _midday():
    from routines.midday import run
    _run_guarded("midday", run)


def _eod():
    from routines.eod import run
    _run_guarded("EOD", run)


# ------------------------------------------------------------------
# Schedule wiring — convert ET times to machine local time
# ------------------------------------------------------------------

schedule.every().day.at(_et_hhmm_to_local("08:00")).do(_premarket)
schedule.every().day.at(_et_hhmm_to_local("12:30")).do(_midday)
schedule.every().day.at(_et_hhmm_to_local("15:50")).do(_eod)


def _handle_signal(signum, frame):
    global _STOP
    log.info("Shutdown signal received — stopping scheduler")
    _STOP = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def run():
    log.info(
        "Scheduler started — routines: premarket@08:00, midday@12:30, EOD@15:50 ET"
    )
    log.info("Press Ctrl-C to stop")
    while not _STOP:
        schedule.run_pending()
        time.sleep(30)
    log.info("Scheduler stopped")


if __name__ == "__main__":
    run()
