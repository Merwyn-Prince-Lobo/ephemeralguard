"""
EphemeralGuard - Module 3
Tamper-proof audit logger with KMS-encrypted S3 dump and DynamoDB event log
"""

import boto3
from botocore.config import Config
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone


def load_env():
    """Read KMS_KEY_ID written by setup_localstack.sh, same as module4/deploy_lambda.py."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    values = {}
    if not os.path.exists(env_path):
        raise SystemExit(
            f"[\u2717] {env_path} not found \u2014 run ./setup_localstack.sh first "
            "(it creates the bucket/table/queue/KMS key and writes .env)."
        )
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line:
                k, v = line.split("=", 1)
                values[k] = v
    if "KMS_KEY_ID" not in values:
        raise SystemExit("[\u2717] KMS_KEY_ID missing from .env \u2014 re-run ./setup_localstack.sh")
    return values


# \u2500\u2500 Config \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
ENDPOINT       = "http://localhost:4566"
REGION         = "us-east-1"
BUCKET         = "ephemeralguard-forensics"
TABLE          = "ForensicAuditLog"
KMS_KEY_ID     = load_env()["KMS_KEY_ID"]

BOTO_KWARGS = dict(
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
    # Default max_pool_connections is 10 — right at the ceiling for
    # PARALLEL=10 benchmark threads each needing s3+dynamodb+kms
    # connections simultaneously. Raised to give headroom under load.
    config=Config(max_pool_connections=50),
)

# ── Clients ───────────────────────────────────────────────────────────────────
s3       = boto3.client("s3",       **BOTO_KWARGS)
dynamodb = boto3.resource("dynamodb", **BOTO_KWARGS)
kms      = boto3.client("kms",      **BOTO_KWARGS)
table    = dynamodb.Table(TABLE)


def compute_hash(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def encrypt_payload(data: str) -> bytes:
    response = kms.encrypt(KeyId=KMS_KEY_ID, Plaintext=data.encode())
    return response["CiphertextBlob"]


def dump_to_s3(event_id: str, encrypted_blob: bytes) -> str:
    s3_key = f"dumps/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{event_id}.enc"
    s3.put_object(
        Bucket=BUCKET,
        Key=s3_key,
        Body=encrypted_blob,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=KMS_KEY_ID,
    )
    return s3_key


def log_audit_event(event_type: str, function_name: str, metadata: dict) -> str:
    event_id  = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    payload = {
        "event_id":      event_id,
        "timestamp":     timestamp,
        "event_type":    event_type,
        "function_name": function_name,
        "metadata":      metadata,
    }
    payload_str  = json.dumps(payload, sort_keys=True)
    payload_hash = compute_hash(payload_str)

    encrypted = encrypt_payload(payload_str)
    s3_key    = dump_to_s3(event_id, encrypted)

    table.put_item(Item={
        "EventId":      event_id,
        "Timestamp":    timestamp,
        "EventType":    event_type,
        "FunctionName": function_name,
        "PayloadHash":  payload_hash,
        "S3Key":        s3_key,
        "Metadata":     metadata,
    })

    print(f"[✓] Event logged: {event_id}")
    print(f"    Type     : {event_type}")
    print(f"    Function : {function_name}")
    print(f"    Hash     : {payload_hash[:16]}...")
    print(f"    S3 Key   : {s3_key}")
    return event_id


def verify_event(event_id: str) -> bool:
    record = table.get_item(Key={"EventId": event_id}).get("Item")
    if not record:
        print(f"[✗] Event {event_id} not found")
        return False

    s3_obj    = s3.get_object(Bucket=BUCKET, Key=record["S3Key"])
    encrypted = s3_obj["Body"].read()
    decrypted = kms.decrypt(CiphertextBlob=encrypted)["Plaintext"].decode()

    recomputed = compute_hash(decrypted)
    stored     = record["PayloadHash"]

    if recomputed == stored:
        print(f"[✓] Tamper check PASSED for {event_id}")
        return True
    else:
        print(f"[✗] TAMPER DETECTED for {event_id}!")
        return False
