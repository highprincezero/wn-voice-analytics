"""Applicable only for cloud provisioning. This file is used only when deployed to the cloud with a multi-region deployment.

Publish a job onto Azure Service Bus. Local compose uses Celery instead.
"""

import json

from app.config import get_settings


def publish_job(queue: str, message: dict) -> None:
    from azure.servicebus import ServiceBusClient, ServiceBusMessage

    settings = get_settings()
    if not settings.service_bus_connection_string:
        raise RuntimeError("SERVICE_BUS_CONNECTION_STRING is required")
    payload = json.dumps(message)
    with ServiceBusClient.from_connection_string(settings.service_bus_connection_string) as client:
        with client.get_queue_sender(queue_name=queue) as sender:
            sender.send_messages(ServiceBusMessage(payload))
