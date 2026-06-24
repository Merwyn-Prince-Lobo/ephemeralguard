"""
EphemeralGuard - Module 3
Tamper-proof audit logger with KMS-encrypted S3 dump and DynamoDB event log
"""

import boto3
import hashlib
import json
import uuid
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
ENDPOINT       = "http://localhost:4566"
REGION         = "us-east-1"
BUCKET         = "ephemeralguard-forensics"
TABLE          = "ForensicAuditLog"
KMS_KEY_ID     = "47237a51-4083-4834-81de-2044bbfada20"

BOTO_KWARGS = dict(
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
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
