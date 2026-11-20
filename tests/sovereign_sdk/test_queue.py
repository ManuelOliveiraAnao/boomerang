"""Queue adapter tests. Each test runs twice:
  [fake]     against an in-memory broker model (always)
  [rabbitmq] against a real RabbitMQ (`boomerang lab up --adapters`; skipped if not running)
"""
import socket
import threading
import time
import uuid

import pytest

from boomerang import lab
from sovereign_sdk import queue as q
from tests.sovereign_sdk.fake_amqp import FakeBroker


def _rabbit_up() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 55672)) == 0


@pytest.fixture(params=["fake", "rabbitmq"])
def make(request):
    """Returns make(max_receive_count) -> (client, queue_url, name)."""
    created = []
    if request.param == "fake":
        broker = FakeBroker()
        factory = broker.channel
    else:
        if not _rabbit_up():
            pytest.skip("RabbitMQ not running: `boomerang lab up --adapters` (needs Docker)")
        factory = None

    def _make(max_receive_count=None):
        name = f"t-{uuid.uuid4().hex[:8]}"
        client = q.Queue(url=lab.TEST_AMQP_URL, channel_factory=factory)
        client.declare(name, max_receive_count)
        created.append(client)
        return client, client.get_queue_url(QueueName=name)["QueueUrl"], name

    yield _make
    for c in created:
        c.close()


def test_send_receive_delete(make):
    c, url, name = make()
    sent = c.send_message(QueueUrl=url, MessageBody='{"order_id": "a1"}')
    got = c.receive_message(QueueUrl=url)["Messages"]
    assert len(got) == 1 and got[0]["Body"] == '{"order_id": "a1"}'
    assert got[0]["MessageId"] == sent["MessageId"]
    assert got[0]["MD5OfBody"] == sent["MD5OfMessageBody"]
    c.delete_message(QueueUrl=url, ReceiptHandle=got[0]["ReceiptHandle"])
    assert c.depth(name) == 0
    assert c.receive_message(QueueUrl=url) == {}


def test_empty_receive_is_fast_and_has_no_messages_key(make):
    c, url, _ = make()
    t = time.monotonic()
    assert c.receive_message(QueueUrl=url, WaitTimeSeconds=0) == {}
    assert time.monotonic() - t < 0.5


def test_long_poll_waits(make):
    c, url, _ = make()
    t = time.monotonic()
    assert c.receive_message(QueueUrl=url, WaitTimeSeconds=1) == {}
    assert 0.9 <= time.monotonic() - t < 2.5


def test_long_poll_returns_early_when_message_arrives(make):
    c, url, _ = make()
    threading.Timer(0.3, lambda: c.send_message(QueueUrl=url, MessageBody="late")).start()
    t = time.monotonic()
    got = c.receive_message(QueueUrl=url, WaitTimeSeconds=3)
    assert got["Messages"][0]["Body"] == "late"
    assert time.monotonic() - t < 2


def test_redelivery_after_visibility_timeout(make):
    c, url, _ = make()
    c.send_message(QueueUrl=url, MessageBody="x")
    first = c.receive_message(QueueUrl=url, VisibilityTimeout=1)["Messages"][0]
    assert c.receive_message(QueueUrl=url) == {}  # invisible while in flight
    time.sleep(1.2)
    again = c.receive_message(QueueUrl=url)["Messages"][0]
    assert again["MessageId"] == first["MessageId"]
    assert again["ReceiptHandle"] != first["ReceiptHandle"]


def test_stale_handle_delete_is_harmless(make):
    c, url, name = make()
    c.send_message(QueueUrl=url, MessageBody="x")
    first = c.receive_message(QueueUrl=url, VisibilityTimeout=0)["Messages"][0]
    second = c.receive_message(QueueUrl=url)["Messages"][0]  # first expired -> redelivered
    assert c.delete_message(QueueUrl=url, ReceiptHandle=first["ReceiptHandle"]) == {}
    c.delete_message(QueueUrl=url, ReceiptHandle=second["ReceiptHandle"])
    assert c.depth(name) == 0


def test_dead_letter_after_max_receives(make):
    """SQS semantics: maxReceiveCount=3 -> received 3 times, then it moves to the DLQ."""
    c, url, name = make(max_receive_count=3)
    c.send_message(QueueUrl=url, MessageBody="poison")
    receives = 0
    for _ in range(6):
        got = c.receive_message(QueueUrl=url, VisibilityTimeout=0)
        if not got:
            break
        receives += 1
    c._expire()
    assert receives == 3
    assert c.depth(name) == 0
    assert c.depth(q.dlq_name(name)) == 1


def test_max_number_of_messages(make):
    c, url, _ = make()
    for i in range(5):
        c.send_message(QueueUrl=url, MessageBody=str(i))
    assert len(c.receive_message(QueueUrl=url, MaxNumberOfMessages=3)["Messages"]) == 3


def test_thread_safety(make):
    c, url, name = make()

    def worker():
        for i in range(25):
            c.send_message(QueueUrl=url, MessageBody=str(i))
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.depth(name) == 100


def test_unknown_queue(make):
    c, _, _ = make()
    with pytest.raises(q.QueueDoesNotExist):
        c.get_queue_url(QueueName="does-not-exist-" + uuid.uuid4().hex[:6])
    # after the broker closes the channel (404), the adapter must reconnect transparently
    other = f"t-{uuid.uuid4().hex[:8]}"
    c.declare(other)
    assert c.get_queue_url(QueueName=other)["QueueUrl"].endswith(other)


def test_unsupported_features(make):
    c, url, _ = make()
    with pytest.raises(q.UnsupportedFeature):
        c.send_message(QueueUrl=url, MessageBody="x", DelaySeconds=5)
    with pytest.raises(q.UnsupportedFeature):
        c.send_message(QueueUrl=url, MessageBody="x", MessageGroupId="g")


def test_declare_rejects_mismatched_arguments(make):
    c, _, name = make(max_receive_count=3)
    c.declare(name, 3)  # same args: fine
    with pytest.raises(Exception):
        c.declare(name, 5)


def test_lazy_connect(monkeypatch):
    monkeypatch.setenv("AMQP_URL", "amqp://nobody:nothing@127.0.0.1:1/")
    client = q.Queue()  # must not connect yet
    assert client._ch is None


def test_queue_arguments_contract():
    assert q.queue_arguments("order-events", 3) == {
        "x-queue-type": "quorum", "x-delivery-limit": 2,
        "x-dead-letter-exchange": "", "x-dead-letter-routing-key": "order-events-dlq"}
    assert q.queue_arguments("x", None) == {"x-queue-type": "quorum"}
