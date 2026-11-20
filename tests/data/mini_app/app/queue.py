"""Order events on SQS (ShopLite shape)."""
import json
import os

import boto3

sqs = boto3.client("sqs")


def _queue_url():
    return sqs.get_queue_url(QueueName=os.environ.get("ORDER_EVENTS_QUEUE", "order_events"))["QueueUrl"]


def publish_order_created(order_id):
    sqs.send_message(QueueUrl=_queue_url(), MessageBody=json.dumps({"order_id": order_id}))


def receive_one(wait_seconds=1):
    resp = sqs.receive_message(QueueUrl=_queue_url(), MaxNumberOfMessages=1, WaitTimeSeconds=wait_seconds,
                               VisibilityTimeout=30)
    messages = resp.get("Messages", [])
    if not messages:
        return None
    return json.loads(messages[0]["Body"])["order_id"], messages[0]["ReceiptHandle"]


def ack(receipt_handle):
    sqs.delete_message(QueueUrl=_queue_url(), ReceiptHandle=receipt_handle)
