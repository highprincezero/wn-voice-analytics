from app.analysis.service import run_file_analysis
from app.jobs.celery_app import celery_app
from app.jobs.summary_job import scheduled_rollup_all_users


@celery_app.task(name="voice.analyze_file", bind=True, max_retries=2)
def analyze_file_task(self, file_id: str, user_id: str) -> None:
    try:
        run_file_analysis(file_id, user_id)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=5) from exc


@celery_app.task(name="voice.scheduled_rollup")
def scheduled_rollup_task() -> int:
    return scheduled_rollup_all_users()
