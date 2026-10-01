from app.analysis.progress import record_job_note
from app.analysis.service import run_file_analysis
from app.jobs.celery_app import celery_app
from app.jobs.report_job import run_group_report
from app.jobs.summary_job import scheduled_rollup_all_users


# Registers a Celery task. name= is the stable id used in messages;
# bind=True passes the task instance as `self`; max_retries caps retry attempts.
@celery_app.task(name="voice.analyze_file", bind=True, max_retries=2)
def analyze_file_task(self, file_id: str, user_id: str) -> None:
    try:
        # self.request holds the delivery info (task id, retry count).
        run_file_analysis(file_id, user_id, task_id=self.request.id)
    except Exception as exc:
        retries = int(getattr(self.request, "retries", 0) or 0)
        limit = int(self.max_retries)
        if retries >= limit:
            record_job_note(
                user_id,
                file_id,
                f"Retries exhausted: {type(exc).__name__}",
                level="error",
            )
            raise
        record_job_note(
            user_id,
            file_id,
            f"Retry {retries + 1} scheduled after {type(exc).__name__}",
            level="error",
        )
        # self.retry re-queues this task after `countdown` seconds; it raises a Retry
        # exception, so `raise` makes the control flow explicit.
        raise self.retry(exc=exc, countdown=5) from exc


# Periodic task triggered by the beat schedule in celery_app.py.
@celery_app.task(name="voice.scheduled_rollup")
def scheduled_rollup_task() -> int:
    return scheduled_rollup_all_users()


# All groupings report: one background job per request.
@celery_app.task(name="voice.group_report")
def group_report_task(report_id: str, user_id: str) -> None:
    run_group_report(report_id, user_id)
