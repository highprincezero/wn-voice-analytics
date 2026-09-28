from celery import Celery

from app.config import get_settings

settings = get_settings()
celery_app = Celery(
    "voice",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_acks_late=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
    beat_schedule_filename="/tmp/celerybeat-schedule",
    beat_schedule={
        "scheduled-rollup": {
            "task": "voice.scheduled_rollup",
            "schedule": float(settings.rollup_schedule_seconds),
        }
    },
)


def _register_tasks() -> None:
    from app.jobs import tasks  # noqa: F401


_register_tasks()
