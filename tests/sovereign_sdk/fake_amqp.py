"""In-memory stand-in for a pika BlockingChannel, for testing the queue adapter without RabbitMQ.

Models the RabbitMQ behavior the adapter relies on: durable quorum queues, basic_get with manual
ack, nack+requeue incrementing the delivery count, and dead-lettering once the count exceeds
x-delivery-limit. Real-broker tests (same test file) confirm this model when Docker is available.
"""
from __future__ import annotations

import collections
import itertools
import threading
from types import SimpleNamespace

from pika.exceptions import ChannelClosedByBroker, StreamLostError, UnroutableError


class FakeBroker:
    def __init__(self):
        self.queues: dict[str, collections.deque] = {}
        self.args: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.tags = itertools.count(1)

    def channel(self) -> "FakeChannel":
        return FakeChannel(self)


class FakeChannel:
    def __init__(self, broker: FakeBroker):
        self.b = broker
        self.is_open = True
        self.unacked: dict[int, tuple[str, dict]] = {}

    def drop(self):
        """Simulate a lost TCP connection: unacked messages go back to their queues."""
        with self.b.lock:
            for queue, msg in self.unacked.values():
                self.b.queues[queue].appendleft(msg)
            self.unacked.clear()
        self.dropped = True

    def _check(self):
        if getattr(self, "dropped", False):
            raise StreamLostError("connection lost")

    def _closed(self, code, text):
        self.is_open = False
        raise ChannelClosedByBroker(code, text)

    def queue_declare(self, queue, passive=False, durable=False, arguments=None):
        self._check()
        with self.b.lock:
            if passive:
                if queue not in self.b.queues:
                    self._closed(404, f"NOT_FOUND - no queue '{queue}'")
            elif queue in self.b.queues:
                if (arguments or {}) != self.b.args[queue]:
                    self._closed(406, "PRECONDITION_FAILED - inequivalent arg")
            else:
                self.b.queues[queue] = collections.deque()
                self.b.args[queue] = arguments or {}
            return SimpleNamespace(method=SimpleNamespace(message_count=len(self.b.queues[queue])))

    def basic_publish(self, exchange, routing_key, body, properties=None, mandatory=False):
        self._check()
        with self.b.lock:
            if routing_key not in self.b.queues:
                raise UnroutableError([])  # what pika raises for mandatory=True + confirms
            self.b.queues[routing_key].append({"body": body, "props": properties, "count": 0})

    def basic_get(self, queue, auto_ack=False):
        self._check()
        with self.b.lock:
            if queue not in self.b.queues:
                self._closed(404, f"NOT_FOUND - no queue '{queue}'")
            if not self.b.queues[queue]:
                return None, None, None
            msg = self.b.queues[queue].popleft()
            tag = next(self.b.tags)
            self.unacked[tag] = (queue, msg)
            return SimpleNamespace(delivery_tag=tag), msg["props"], msg["body"]

    def basic_ack(self, delivery_tag):
        self._check()
        with self.b.lock:
            self.unacked.pop(delivery_tag)

    def basic_nack(self, delivery_tag, requeue=True):
        self._check()
        with self.b.lock:
            queue, msg = self.unacked.pop(delivery_tag)
            msg["count"] += 1
            args = self.b.args[queue]
            limit = args.get("x-delivery-limit")
            if limit is not None and msg["count"] > limit:
                self.b.queues[args["x-dead-letter-routing-key"]].append(msg)
            elif requeue:
                self.b.queues[queue].appendleft(msg)
