"""AWS resources in moto from the PAM + demo data. Owner A, task A3.

Spec: docs/03 §3.10, docs/04 §5, docs/15 L5/L17. Stub created by C1: A replaces the bodies;
signatures here are proposed (docs/03 gives none).

L17: never import the sample app into the engine process; seed with plain boto3 or a subprocess.
"""

from __future__ import annotations

from boomerang._stub import NotBuilt
from boomerang.events import Emitter
from boomerang.models import AppModel


def provision(pam: AppModel, emit: Emitter | None = None) -> None:
    """Bucket, table (PAY_PER_REQUEST, GSIs projection ALL), DLQ + queue with RedrivePolicy and VisibilityTimeout."""
    raise NotBuilt("provisioner_aws.provision", owner="A", doc="docs/03 §3.10")


def seed(pam: AppModel, count: int = 25, emit: Emitter | None = None) -> dict[str, int]:
    """``count`` orders (some processed, some pending) with receipts, 3 messages left in the queue."""
    raise NotBuilt("provisioner_aws.seed", owner="A", doc="docs/04 §5")
