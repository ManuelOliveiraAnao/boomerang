"""sovereign_sdk: small, tested adapters that mimic boto3 (docs/02 §5).

- ``kv``      → Postgres      (owner B)
- ``queue``   → RabbitMQ      (owner A)
- ``storage`` → S3-compatible (owner C)

Submodules are not imported here on purpose: ``from sovereign_sdk import kv`` loads only what the
app uses, and a missing driver for one adapter never breaks the others.
"""
