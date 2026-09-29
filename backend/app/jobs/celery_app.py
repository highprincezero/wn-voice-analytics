from celery import Celery
from celery.signals import worker_process_init

from app.config import get_settings
from app.telemetry import init_telemetry

settings = get_settings()
# Celery app: broker = queue that carries task messages (Redis here),
# backend = where task results/states are stored.
celery_app = Celery(
    "voice",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
# Global Celery settings.
celery_app.conf.update(
    # Ack the message only after the task finishes, so a crashed worker's job is redelivered.
    task_acks_late=True,
    # We don't need return values stored; status lives in Postgres instead.
    task_ignore_result=True,
    # Each worker process reserves one task at a time (fair for long-running jobs).
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
    # Local file where celery beat keeps its last-run times.
    beat_schedule_filename="/tmp/celerybeat-schedule",
    # Beat schedule: the beat process enqueues this task every N seconds.
    beat_schedule={
        "scheduled-rollup": {
            "task": "voice.scheduled_rollup",
            "schedule": float(settings.rollup_schedule_seconds),
        }
    },
)


# Signal handler: runs once in each worker child process after fork,
# so telemetry exporters/threads are created per process, not shared.
@worker_process_init.connect
def _init_worker_telemetry(**_kwargs) -> None:
    init_telemetry()


# Import tasks.py so its @celery_app.task functions get registered with the app.
# Done inside a function (late) to avoid a circular import with tasks.py.
def _register_tasks() -> None:
    # noqa: F401 tells ruff the 'unused' import is intentional.
    from app.jobs import tasks  # noqa: F401


_register_tasks()
