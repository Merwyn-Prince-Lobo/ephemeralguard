"""
EphemeralGuard - Module 4
The actual Lambda function — triggered on suspicious event,
runs triage, logs via Module 3
"""

import os
import sys
import json
import time
import hashlib
import struct
import random
import numpy as np
import boto3
import uuid
from datetime import datetime, timezone

ENDPOINT   = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION     = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
BUCKET     = os.environ.get("BUCKET", "ephemeralguard-forensics")
TABLE      = os.environ.get("TABLE", "ForensicAuditLog")
KMS_KEY_ID = os.environ.get("KMS_KEY_ID", "28c7c3c0-27c4-49a5-8f3b-0af3b08574b3")

PAGE_SIZE         = 4096
ENTROPY_THRESHOLD = 1.0


def calculate_entropy(page):
    arr    = np.frombuffer(page, dtype=np.uint8)
    counts = np.bincount(arr, minlength=256)
    probs  = counts[counts > 0] / arr.size
    return float(-np.sum(probs * np.log2(probs)))


def generate_fake_dump(size_mb=5):
    pages = []
    total = int((size_mb * 1024 * 1024) / PAGE_SIZE)
    for _ in range(total):
        r = random.random()
        if r < 0.40:
            pages.append(b'\x00' * PAGE_SIZE)
        elif r < 0.70:
            pages.append(bytes([random.randint(32, 126)]) * PAGE_SIZE)
        elif r < 0.90:
            pages.append(os.urandom(PAGE_SIZE))
        else:
            pid  = random.randint(1, 65535)
            page = struct.pack('>HH', 0xDEAD, pid) + os.urandom(PAGE_SIZE - 4)
            pages.append(page)
    return b''.join(pages)


def triage_dump(dump_bytes):
    kept, dropped = 0, 0
    page_hashes, manifest, entropy_values = [], [], []

    for i in range(0, len(dump_bytes), PAGE_SIZE):
        page = dump_bytes[i:i + PAGE_SIZE]
        if len(page) < PAGE_SIZE:
            break
        arr = np.frombuffer(page, dtype=np.uint8)
        if not arr.any():
            dropped += 1
            continue
        entropy = calculate_entropy(page)
        if entropy < ENTROPY_THRESHOLD:
            dropped += 1
            continue
        page_hash = hashlib.sha256(page).digest()
        page_hashes.append(page_hash)
        manifest.append({
            "page_number": i // PAGE_SIZE,
            "entropy":     round(entropy, 4),
            "sha256":      page_hash.hex()
        })
        entropy_values.append(entropy)
        kept += 1

    current = page_hashes[:]
    while len(current) > 1:
        if len(current) % 2:
            current.append(current[-1])
        current = [
            hashlib.sha256(current[j] + current[j+1]).digest()
            for j in range(0, len(current), 2)
        ]
    merkle_root = current[0].hex() if current else None

    return {
        "merkle_root":   merkle_root,
        "pages_kept":    kept,
        "pages_dropped": dropped,
        "avg_entropy":   round(float(np.mean(entropy_values)), 4) if entropy_values else 0,
        "manifest":      manifest[:10]
    }


def log_audit_event(event_type, function_name, metadata):
    kwargs = dict(
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3       = boto3.client("s3",        **kwargs)
    dynamodb = boto3.resource("dynamodb", **kwargs)
    kms      = boto3.client("kms",       **kwargs)
    table    = dynamodb.Table(TABLE)

    event_id  = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    payload   = json.dumps({
        "event_id": event_id, "timestamp": timestamp,
        "event_type": event_type, "function_name": function_name,
        "metadata": metadata
    }, sort_keys=True)

    payload_hash = hashlib.sha256(payload.encode()).hexdigest()
    encrypted    = kms.encrypt(KeyId=KMS_KEY_ID, Plaintext=payload.encode())["CiphertextBlob"]
    s3_key       = f"dumps/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{event_id}.enc"

    s3.put_object(Bucket=BUCKET, Key=s3_key, Body=encrypted,
                  ServerSideEncryption="aws:kms", SSEKMSKeyId=KMS_KEY_ID)
    table.put_item(Item={
        "EventId": event_id, "Timestamp": timestamp,
        "EventType": event_type, "FunctionName": function_name,
        "PayloadHash": payload_hash, "S3Key": s3_key, "Metadata": metadata
    })
    return event_id


def lambda_handler(event, context):
    start = time.perf_counter()

    # SQS-triggered invocations wrap the real payload as a JSON string in
    # Records[0]["body"]; direct invocations (e.g. deploy_lambda.py's test
    # call) pass function_name/trigger at the top level. Handle both.
    if "Records" in event and event["Records"]:
        try:
            payload = json.loads(event["Records"][0].get("body", "{}"))
        except (json.JSONDecodeError, TypeError):
            payload = {}
    else:
        payload = event

    function_name = payload.get("function_name", "unknown-lambda")
    trigger       = payload.get("trigger", "manual")

    print(f"[EphemeralGuard] Triggered for {function_name} via {trigger}")

    dump       = generate_fake_dump(size_mb=5)
    triage     = triage_dump(dump)
    elapsed_ms = (time.perf_counter() - start) * 1000

    event_id = log_audit_event(
        event_type="MEMORY_DUMP",
        function_name=function_name,
        metadata={
            "trigger":       trigger,
            "merkle_root":   triage["merkle_root"],
            "pages_kept":    triage["pages_kept"],
            "pages_dropped": triage["pages_dropped"],
            "avg_entropy":   triage["avg_entropy"],
            "capture_ms":    round(elapsed_ms, 2),
        }
    )

    return {
        "statusCode": 200,
        "event_id":   event_id,
        "capture_ms": round(elapsed_ms, 2),
        "triage":     triage
    }