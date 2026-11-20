"""SQS-like queue adapter on RabbitMQ (pika). Owner A, task A4.

Spec: docs/02 §5, docs/15 L15. Stub created by C1: A replaces the bodies.

Proposed for A (L15): ``client()`` and ``get_queue_url()`` open no connection; the first real call does;
a dropped connection (missed heartbeats) is reopened once; a lock guards the shared BlockingConnection,
which isn't thread-safe (FastAPI runs sync routes in a thread pool).
"""

from __future__ import annotations

from boomerang._stub import NotBuilt

_DOC = "docs/02 §5"


def client() -> Queue:
    """Reads AMQP_URL."""
    raise NotBuilt("sovereign_sdk.queue.client", owner="A", doc=_DOC)


class Queue:
    def get_queue_url(self, QueueName: str) -> dict:
        raise NotBuilt("sovereign_sdk.queue.Queue.get_queue_url", owner="A", doc=_DOC)

    def send_message(self, QueueUrl: str, MessageBody: str) -> dict:
        raise NotBuilt("sovereign_sdk.queue.Queue.send_message", owner="A", doc=_DOC)

    def receive_message(self, QueueUrl, MaxNumberOfMessages=1, WaitTimeSeconds=0, VisibilityTimeout=None) -> dict:
        raise NotBuilt("sovereign_sdk.queue.Queue.receive_message", owner="A", doc=_DOC)

    def delete_message(self, QueueUrl, ReceiptHandle) -> dict:
        raise NotBuilt("sovereign_sdk.queue.Queue.delete_message", owner="A", doc=_DOC)
