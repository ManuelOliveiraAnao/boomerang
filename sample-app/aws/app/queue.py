"""Order events (Amazon SQS)."""
import json
import os

import boto3

sqs = boto3.client("sqs")
_queue_url: str | None = None


def _url() -> str:
    global _queue_url
    if _queue_url is None:
        _queue_url = sqs.get_queue_url(QueueName=os.environ["ORDER_EVENTS_QUEUE"])["QueueUrl"]
    return _queue_url


def publish_order_created(order_id: str) -> None:
    sqs.send_message(QueueUrl=_url(), MessageBody=json.dumps({"order_id": order_id}))


def receive_one(wait_seconds: int) -> tuple[str, str] | None:
    resp = sqs.receive_message(QueueUrl=_url(), MaxNumberOfMessages=1,
                               WaitTimeSeconds=wait_seconds, VisibilityTimeout=30)
    messages = resp.get("Messages", [])
    if not messages:
        return None
    msg = messages[0]
    return json.loads(msg["Body"])["order_id"], msg["ReceiptHandle"]


def ack(receipt_handle: str) -> None:
    sqs.delete_message(QueueUrl=_url(), ReceiptHandle=receipt_handle)
