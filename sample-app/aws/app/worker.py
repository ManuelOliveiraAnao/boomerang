"""Background worker: marks orders as processed."""
from datetime import datetime, timezone

from . import db, queue


def process_once(wait_seconds: int = 1) -> str | None:
    received = queue.receive_one(wait_seconds)
    if received is None:
        return None
    order_id, handle = received
    db.set_status(order_id, "processed", datetime.now(timezone.utc).isoformat())
    queue.ack(handle)
    return order_id


def main() -> None:
    while True:
        process_once(wait_seconds=10)


if __name__ == "__main__":
    main()
