"""Optional scheduled (end-of-day) scans driven by config.schedule."""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import load_config
from .scanner import run_scan_sync

_scheduler = None
_job = None


def init_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
    reschedule(load_config())


def shutdown_scheduler():
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)


def reschedule(config):
    """Apply the schedule section of the config (called on every save)."""
    global _job
    if _scheduler is None:
        return
    if _job is not None:
        try:
            _job.remove()
        except Exception:  # noqa: BLE001
            pass
        _job = None

    schedule = config.get("schedule", {})
    if not schedule.get("enabled"):
        return
    try:
        hour, minute = str(schedule.get("time", "16:30")).split(":")
        trigger = CronTrigger(
            day_of_week="mon-fri",
            hour=int(hour),
            minute=int(minute),
            timezone=schedule.get("timezone", "America/New_York"),
        )
        _job = _scheduler.add_job(run_scan_sync, trigger, id="scheduled_scan",
                                  replace_existing=True)
    except Exception:  # noqa: BLE001 - a bad schedule must not crash the app
        _job = None
