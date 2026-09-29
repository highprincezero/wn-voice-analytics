"""Applicable only for cloud provisioning. This file is used only when deployed to the cloud with a multi-region deployment.

Long-running production worker.

Local compose uses Celery. This process is the Container Apps command: it reads
Azure Service Bus queues and calls the same job functions.
"""

import json
import logging

from app.config import get_settings
from app.jobs.dispatcher import dispatch

logger = logging.getLogger(__name__)
QUEUES = ("transcription", "llm-layer1", "llm-layer2", "rollup")


def message_json(body: object) -> dict:
    if isinstance(body, bytes):
        text = body.decode("utf-8")
    elif isinstance(body, str):
        text = body
    else:
        parts = []
        for part in body:
            parts.append(part if isinstance(part, bytes) else bytes(part))
        text = b"".join(parts).decode("utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("message body must be an object")
    return payload


def serve_forever() -> None:
    from azure.servicebus import ServiceBusClient

    settings = get_settings()
    if not settings.service_bus_connection_string:
        raise RuntimeError("SERVICE_BUS_CONNECTION_STRING is required")
    client = ServiceBusClient.from_connection_string(settings.service_bus_connection_string)
    with client:
        while True:
            for queue in QUEUES:
                receiver = client.get_queue_receiver(queue_name=queue, max_wait_time=1)
                with receiver:
                    for message in receiver:
                        try:
                            dispatch(message_json(message.body), queue=queue)
                            receiver.complete_message(message)
                        except Exception:
                            logger.exception("job failed on %s", queue)
                            _note_abandon(message)
                            receiver.abandon_message(message)


def _note_abandon(message) -> None:
    try:
        payload = message_json(message.body)
    except Exception:
        logger.exception("could not read abandoned message")
        return
    file_id = payload.get("file_id")
    user_id = payload.get("user_id")
    if not file_id or not user_id:
        return
    from app.analysis.progress import record_job_note

    record_job_note(
        user_id,
        file_id,
        "Service Bus message abandoned for retry",
        level="error",
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    serve_forever()


if __name__ == "__main__":
    main()
