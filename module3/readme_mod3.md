# EphemeralGuard — Module 3: Tamper-Proof Storage & Audit Logging

> **Owner:** Merwyn (PES2UG24CS276)
> **Stack:** Python · boto3 · AWS S3 · DynamoDB · KMS · LocalStack

---

## What This Module Does

Module 3 is the **evidence preservation layer** of EphemeralGuard. When a suspicious Lambda event is detected, this module:

1. Builds a forensic payload from the event
2. SHA-256 hashes it (tamper fingerprint)
3. Encrypts it with KMS
4. Dumps the encrypted blob to S3
5. Writes an audit record to DynamoDB
6. Can verify at any time that evidence hasn't been touched

---

## File Structure

```
module3/
├── audit_logger.py     # Core module — import this from other modules
└── test_module3.py     # Standalone test / demo script
```

---

## How to Use From Other Modules

Just import `log_audit_event` and call it. That's it.

```python
from module3.audit_logger import log_audit_event, verify_event

# Log a forensic event
event_id = log_audit_event(
    event_type="YOUR_EVENT_TYPE",   # e.g. "MEMORY_DUMP", "ANOMALY_DETECTED"
    function_name="your-lambda-name",
    metadata={
        # any dict — put whatever context you want preserved
        "key": "value"
    }
)

# Verify evidence integrity later
verify_event(event_id)  # returns True/False
```

Returns the `event_id` (UUID string) — **store this** if you need to verify later.

---

## Integration Points by Module

### Module 1 (Sinchana) — eBPF / Falco Event Detection
Call `log_audit_event` when Falco fires an alert:
```python
log_audit_event(
    event_type="FALCO_ALERT",
    function_name=falco_event["output_fields"]["proc.name"],
    metadata={
        "rule":     falco_event["rule"],
        "priority": falco_event["priority"],
        "output":   falco_event["output"],
    }
)
```

### Module 2 (Karthik) — Memory Capture / SQS
Call `log_audit_event` after a memory dump is captured and queued:
```python
log_audit_event(
    event_type="MEMORY_DUMP",
    function_name=lambda_function_name,
    metadata={
        "dump_size_bytes": len(dump_payload),
        "sqs_message_id":  message_id,
        "capture_time_ms": elapsed_ms,
    }
)
```

### Module 4 (Karthik + Merwyn) — IAM Attack Simulation
Call `log_audit_event` when an IAM attack is simulated or detected:
```python
log_audit_event(
    event_type="IAM_ATTACK_SIMULATED",
    function_name="ephemeralguard-monitor",
    metadata={
        "attack_type": "privilege_escalation",
        "action":      "AttachRolePolicy",
        "role":        "lambda-execution-role",
        "policy":      "AdministratorAccess",
        "severity":    "CRITICAL",
    }
)
```

---

## Config (audit_logger.py)

If your environment differs, change these at the top of `audit_logger.py`:

```python
ENDPOINT   = "http://localhost:4566"   # LocalStack. For real AWS, remove this line
REGION     = "us-east-1"
BUCKET     = "ephemeralguard-forensics"
TABLE      = "ForensicAuditLog"
KMS_KEY_ID = "47237a51-4083-4834-81de-2044bbfada20"  # replace with real KMS ARN on AWS
```

For **real AWS** (not LocalStack), remove `endpoint_url` from `BOTO_KWARGS` and use actual IAM credentials instead of `"test"`.

---

## AWS Resources Used

| Resource | Name / ID | Purpose |
|---|---|---|
| S3 Bucket | `ephemeralguard-forensics` | Stores encrypted forensic dumps |
| DynamoDB Table | `ForensicAuditLog` | Stores audit records + hashes |
| KMS Key | `47237a51-4083-4834-81de-2044bbfada20` | Encrypts all forensic payloads |

---

## DynamoDB Record Schema

Each audit event writes one record:

| Field | Type | Description |
|---|---|---|
| `EventId` | String (PK) | UUID — unique per event |
| `Timestamp` | String | ISO 8601 UTC |
| `EventType` | String | e.g. `MEMORY_DUMP`, `IAM_ATTACK_SIMULATED` |
| `FunctionName` | String | Lambda function that triggered the event |
| `PayloadHash` | String | SHA-256 of the original payload |
| `S3Key` | String | Path to encrypted dump in S3 |
| `Metadata` | Map | Arbitrary context from the calling module |

---

## S3 Dump Path Format

```
dumps/YYYY/MM/DD/<event_id>.enc
```

Example:
```
dumps/2026/06/22/8194a219-456a-4d88-acbf-fa0434aff436.enc
```

---

## Tamper Detection — How It Works

```
Stored Hash (DynamoDB)
        ↕  compare
Recomputed Hash ← SHA-256(KMS_decrypt(S3 blob))

Match   → ✅ evidence intact
Mismatch → ❌ TAMPER DETECTED
```

The hash and encrypted data live in **separate services** — an attacker would need to consistently corrupt both S3 and DynamoDB while bypassing KMS access controls to avoid detection.

---

## LocalStack Setup (Dev Environment)

Prereqs: Docker running, LocalStack container up on port 4566.

```bash
# Start LocalStack
cd ~/ephemeralguard
docker compose up -d

# Bootstrap resources (one time)
awslocal s3 mb s3://ephemeralguard-forensics
awslocal dynamodb create-table \
  --table-name ForensicAuditLog \
  --attribute-definitions AttributeName=EventId,AttributeType=S \
  --key-schema AttributeName=EventId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
awslocal kms create-key --description "EphemeralGuard Audit Key"

# Install deps
pip install boto3 awscli awscli-local --break-system-packages
```

---

## Running the Test

```bash
cd ~/ephemeralguard
python3 test_module3.py
```

Expected output:
```
=======================================================
  EphemeralGuard Module 3 - Audit Logger Test
=======================================================
[✓] Event logged: <uuid>
    Type     : MEMORY_DUMP
    Function : fintech-payment-processor
    Hash     : e80c35574328aa6c...
    S3 Key   : dumps/2026/06/22/<uuid>.enc

[✓] Event logged: <uuid>
    Type     : IAM_POLICY_CHANGE
    ...

── Tamper Verification ──────────────────────────────
[✓] Tamper check PASSED for <uuid>
[✓] Tamper check PASSED for <uuid>
```

---

## Dependencies

```
boto3
awscli
awscli-local
```

Install: `pip install boto3 awscli awscli-local --break-system-packages`
