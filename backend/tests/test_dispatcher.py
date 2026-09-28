import pytest

from app.jobs.dispatcher import dispatch
from app.jobs.service_bus_worker import message_json


def test_dispatch_rejects_unknown_kind():
    with pytest.raises(ValueError):
        dispatch({"kind": "nope"})


def test_dispatch_requires_ids():
    with pytest.raises(ValueError):
        dispatch({"kind": "analyze_file"})


def test_message_json_reads_bytes():
    assert message_json(b'{"kind":"rollup"}')["kind"] == "rollup"


def test_service_bus_publish(monkeypatch):
    from app.config import get_settings
    from app.jobs.publisher import publish_job

    sent = {}

    class FakeSender:
        def send_messages(self, message):
            sent["message"] = message

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, connection):
            sent["connection"] = connection

        def get_queue_sender(self, queue_name):
            sent["queue"] = queue_name
            return FakeSender()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeMessage:
        def __init__(self, body):
            self.body = body

    monkeypatch.setattr(get_settings(), "service_bus_connection_string", "Endpoint=sb://example/")
    monkeypatch.setattr(
        "azure.servicebus.ServiceBusClient.from_connection_string",
        lambda connection: FakeClient(connection),
    )
    monkeypatch.setattr("azure.servicebus.ServiceBusMessage", FakeMessage)
    publish_job("transcription", {"kind": "analyze_file", "file_id": "f", "user_id": "u"})
    assert sent["queue"] == "transcription"
    assert "analyze_file" in sent["message"].body
