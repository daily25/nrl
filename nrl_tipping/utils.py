from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nrl_tipping.config import TIP_LOCK_MINUTES

try:
    from zoneinfo import ZoneInfo

    SYDNEY_TZ = ZoneInfo("Australia/Sydney")
except Exception:
    try:
        import pytz

        SYDNEY_TZ = pytz.timezone("Australia/Sydney")
    except Exception:
        # Last-resort fallback if tz database libraries are unavailable.
        SYDNEY_TZ = timezone(timedelta(hours=10), name="AEST")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sydney_now() -> datetime:
    return utc_now().astimezone(SYDNEY_TZ)


def sydney_now_iso() -> str:
    return sydney_now().isoformat()


def display_sydney(value: str) -> str:
    try:
        parsed = parse_iso_datetime(value).astimezone(SYDNEY_TZ)
        return parsed.strftime("%Y-%m-%d %I:%M %p %Z")
    except Exception:
        return value


def tip_lock_deadline_utc(start_time_utc: str, lock_minutes: int = TIP_LOCK_MINUTES) -> datetime:
    kickoff = parse_iso_datetime(start_time_utc)
    return kickoff - timedelta(minutes=max(0, int(lock_minutes)))


def is_tip_locked(start_time_utc: str, now: datetime | None = None, lock_minutes: int = TIP_LOCK_MINUTES) -> bool:
    current = now.astimezone(timezone.utc) if now is not None else utc_now()
    return current >= tip_lock_deadline_utc(start_time_utc, lock_minutes=lock_minutes)
