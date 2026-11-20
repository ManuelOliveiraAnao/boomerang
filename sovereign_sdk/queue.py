"""SQS-compatible queue adapter backed by RabbitMQ (quorum queues + dead-lettering).

Mimics the boto3 SQS client calls ShopLite-style apps use:
    get_queue_url, send_message, receive_message, delete_message

Semantics:
- At-least-once: a received message stays unacked until delete_message.
- Visibility timeout: unacked messages older than VisibilityTimeout are nacked (requeue) on the
  next call to this client, so they are delivered again. If the process dies, RabbitMQ requeues
  them when the connection closes.
- Dead-letter queue: quorum queue `x-delivery-limit` + dead-letter routing to "<name>-dlq".

Honest limits (raise UnsupportedFeature or are not emulated): FIFO queues, DelaySeconds,
message attributes, ChangeMessageVisibility, purge; expiry only happens when the client is called.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from typing import Any, Callable

import pika
from pika.exceptions import AMQPConnectionError, ChannelClosed, ChannelClosedByBroker, ChannelWrongStateError, StreamLostError

DEFAULT_VISIBILITY_S = 30
POLL_INTERVAL_S = 0.1
_UNSUPPORTED_SEND = ("DelaySeconds", "MessageAttributes", "MessageGroupId", "MessageDeduplicationId",
                     "MessageSystemAttributes")
_RECONNECT_ERRORS = (AMQPConnectionError, StreamLostError, ChannelClosed, ChannelWrongStateError)


class QueueDoesNotExist(Exception):
    """Same meaning as SQS QueueDoesNotExist."""


class UnsupportedFeature(Exception):
    """The app uses an SQS feature this adapter does not provide."""


def dlq_name(name: str) -> str:
    return f"{name}-dlq"


def queue_arguments(name: str, max_receive_count: int | None) -> dict[str, Any]:
    """RabbitMQ arguments for a queue. Shared with the Leaseweb generator (definitions.json):
    a mismatch makes RabbitMQ refuse the declare with PRECONDITION_FAILED.

    SQS maxReceiveCount = how many receives are allowed. RabbitMQ x-delivery-limit = how many
    returns are allowed before dead-lettering, i.e. maxReceiveCount - 1."""
    args: dict[str, Any] = {"x-queue-type": "quorum"}
    if max_receive_count:
        args.update({"x-delivery-limit": max(max_receive_count - 1, 0),
                     "x-dead-letter-exchange": "",
                     "x-dead-letter-routing-key": dlq_name(name)})
    return args


def declare(channel, name: str, max_receive_count: int | None = None) -> None:
    """Create the queue (and its DLQ) if missing. Idempotent with identical arguments."""
    if max_receive_count:
        channel.queue_declare(queue=dlq_name(name), durable=True, arguments={"x-queue-type": "quorum"})
    channel.queue_declare(queue=name, durable=True, arguments=queue_arguments(name, max_receive_count))


def _name_from_url(queue_url: str) -> str:
    return queue_url.rstrip("/").rsplit("/", 1)[-1]


def _md5(body: str) -> str:
    return hashlib.md5(body.encode("utf-8")).hexdigest()


class Queue:
    def __init__(self, url: str | None = None, channel_factory: Callable[[], Any] | None = None):
        self._url = url
        self._factory = channel_factory or self._open_channel
        self._lock = threading.RLock()
        self._ch = None
        self._conn = None
        self._epoch = 0  # bumps on reconnect; handles from an old channel are invalid
        self._inflight: dict[str, tuple[int, float, float]] = {}  # handle -> (tag, received_at, visibility)

    # ---------------------------------------------------------------- connection
    def _open_channel(self):
        params = pika.URLParameters(self._url or os.environ["AMQP_URL"])
        params.heartbeat = 0  # BlockingConnection can't service heartbeats while idle
        params.blocked_connection_timeout = 30
        self._conn = pika.BlockingConnection(params)
        ch = self._conn.channel()
        ch.confirm_delivery()
        return ch

    def _channel(self):
        if self._ch is None or not getattr(self._ch, "is_open", True):
            self._ch = self._factory()
            self._epoch += 1
            self._inflight.clear()  # unacked messages on a dead channel are requeued by the broker
        return self._ch

    def _call(self, fn):
        """Run fn(channel) under the lock; reconnect once on a dropped connection."""
        with self._lock:
            try:
                return fn(self._channel())
            except ChannelClosedByBroker:
                self._ch = None
                raise
            except _RECONNECT_ERRORS:
                self._ch = None
                return fn(self._channel())

    def close(self) -> None:
        with self._lock:
            try:
                if self._conn is not None and self._conn.is_open:
                    self._conn.close()
            finally:
                self._ch = self._conn = None
                self._inflight.clear()

    # ---------------------------------------------------------------- SQS API
    def get_queue_url(self, QueueName: str, **_: Any) -> dict:
        def body(ch):
            ch.queue_declare(queue=QueueName, passive=True)
            return {"QueueUrl": f"amqp:///{QueueName}"}
        try:
            return self._call(body)
        except ChannelClosedByBroker as e:
            if e.reply_code == 404:
                raise QueueDoesNotExist(QueueName) from e
            raise

    def send_message(self, QueueUrl: str, MessageBody: str, **kwargs: Any) -> dict:
        unsupported = [k for k in kwargs if k in _UNSUPPORTED_SEND]
        if unsupported:
            raise UnsupportedFeature(f"send_message({', '.join(unsupported)}) is not supported")
        message_id = uuid.uuid4().hex
        name = _name_from_url(QueueUrl)

        def body(ch):
            ch.basic_publish(exchange="", routing_key=name, body=MessageBody.encode("utf-8"),
                             properties=pika.BasicProperties(delivery_mode=2, message_id=message_id),
                             mandatory=True)
        self._call(body)
        return {"MessageId": message_id, "MD5OfMessageBody": _md5(MessageBody)}

    def receive_message(self, QueueUrl: str, MaxNumberOfMessages: int = 1, WaitTimeSeconds: int = 0,
                        VisibilityTimeout: int | None = None, **kwargs: Any) -> dict:
        # MessageAttributeNames / AttributeNames are accepted and ignored: attributes are not stored.
        name = _name_from_url(QueueUrl)
        visibility = DEFAULT_VISIBILITY_S if VisibilityTimeout is None else VisibilityTimeout
        deadline = time.monotonic() + max(WaitTimeSeconds, 0)
        messages: list[dict] = []
        while True:
            self._expire()

            def body(ch):
                got = []
                while len(messages) + len(got) < MaxNumberOfMessages:
                    method, props, payload = ch.basic_get(queue=name, auto_ack=False)
                    if method is None:
                        break
                    handle = f"{self._epoch}:{method.delivery_tag}:{uuid.uuid4().hex[:8]}"
                    self._inflight[handle] = (method.delivery_tag, time.monotonic(), visibility)
                    text = payload.decode("utf-8")
                    got.append({"MessageId": getattr(props, "message_id", None) or uuid.uuid4().hex,
                                "ReceiptHandle": handle, "Body": text, "MD5OfBody": _md5(text)})
                return got
            try:
                messages.extend(self._call(body))
            except ChannelClosedByBroker as e:
                if e.reply_code == 404:
                    raise QueueDoesNotExist(name) from e
                raise
            if messages or time.monotonic() >= deadline:
                break
            time.sleep(POLL_INTERVAL_S)
        return {"Messages": messages} if messages else {}

    def delete_message(self, QueueUrl: str, ReceiptHandle: str, **_: Any) -> dict:
        with self._lock:
            entry = self._inflight.pop(ReceiptHandle, None)
            if entry is None or not ReceiptHandle.startswith(f"{self._epoch}:"):
                return {}  # expired (already redelivered) or from a dropped connection: nothing to ack
            self._channel().basic_ack(delivery_tag=entry[0])
        return {}

    # ---------------------------------------------------------------- helpers
    def _expire(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [h for h, (_, at, vis) in self._inflight.items() if now - at >= vis]
            for h in expired:
                tag = self._inflight.pop(h)[0]
                try:
                    self._channel().basic_nack(delivery_tag=tag, requeue=True)
                except _RECONNECT_ERRORS:
                    self._ch = None  # broker requeues everything unacked on that channel anyway

    def depth(self, QueueName: str) -> int:
        return self._call(lambda ch: ch.queue_declare(queue=QueueName, passive=True).method.message_count)

    def declare(self, QueueName: str, max_receive_count: int | None = None) -> dict:
        self._call(lambda ch: declare(ch, QueueName, max_receive_count))
        return {"QueueUrl": f"amqp:///{QueueName}"}


_client: Queue | None = None
_client_lock = threading.Lock()


def client() -> Queue:
    """Module-level singleton, like boto3.client("sqs"). Connects lazily on first call."""
    global _client
    with _client_lock:
        if _client is None:
            _client = Queue()
        return _client
