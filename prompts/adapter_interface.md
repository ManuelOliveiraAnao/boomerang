# sovereign_sdk/kv.py  (Postgres; replaces the DynamoDB resource)
def resource() -> "KV"                         # reads DATABASE_URL
class KV:   def Table(self, name: str) -> "Table"
class Table:
    def put_item(self, Item: dict, ConditionExpression=None) -> dict
    def get_item(self, Key: dict) -> dict      # {"Item": {...}} or {}
    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames=None,
                    ExpressionAttributeValues=None, ConditionExpression=None) -> dict   # "SET a = :v, #b = :w" only
    def delete_item(self, Key: dict) -> dict
    def query(self, KeyConditionExpression, IndexName=None) -> dict   # {"Items": [...]}; Key(x).eq(v) only
    def scan(self) -> dict                      # {"Items": [...]}
# ConditionExpression: "attribute_not_exists(x)" or "attribute_exists(x)" only.
# Numbers are returned as Decimal, like boto3. Unsupported expressions raise UnsupportedFeature.
# A failed condition raises ConditionalCheckFailedException, a subclass of botocore's ClientError,
# with .response["Error"]["Code"] == "ConditionalCheckFailedException".

# sovereign_sdk/queue.py  (RabbitMQ; replaces the SQS client)
def client() -> "Queue"                        # reads AMQP_URL
class Queue:
    def get_queue_url(self, QueueName: str) -> dict                          # {"QueueUrl": ...}
    def send_message(self, QueueUrl: str, MessageBody: str) -> dict          # {"MessageId": ...}
    def receive_message(self, QueueUrl, MaxNumberOfMessages=1, WaitTimeSeconds=0,
                        VisibilityTimeout=None) -> dict                      # {"Messages": [{"MessageId","ReceiptHandle","Body"}]}
    def delete_message(self, QueueUrl, ReceiptHandle) -> dict
# No message attributes, no FIFO, no delay queues.

# sovereign_sdk/storage.py  (S3-compatible store; S3 code stays unchanged)
def s3_client()                                # a boto3 S3 client for the new endpoint, path-style
