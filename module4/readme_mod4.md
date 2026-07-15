# EphemeralGuard — Module 4: IAM Attack Simulation, Lambda Deployment & Benchmarking

> **Owner:** Merwyn (PES2UG24CS276) + Karthik
> **Stack:** Python · boto3 · AWS Lambda · IAM · STS · LocalStack

---

## What This Module Does

Module 4 is the **attack surface + validation layer** of EphemeralGuard. It:

1. Sets up IAM roles and policies for Lambda execution
2. Deploys the forensic trigger Lambda function
3. Simulates real fintech IAM attack scenarios (logged via Module 3)
4. Benchmarks the full capture pipeline against the sub-100ms deadline

---

## File Structure

```
module4/
├── iam_setup.py        # Creates IAM roles + policies (run first)
├── lambda_function.py  # The forensic Lambda handler
├── deploy_lambda.py    # Packages + deploys Lambda to LocalStack
├── iam_attack_sim.py   # Simulates 4 IAM attack scenarios
└── benchmark.py        # Latency benchmarking + stress test
```

---

## Run Order

Always run in this order:

```bash
cd ~/temp_ccncs

# 1. IAM setup
python3 module4/iam_setup.py

# 2. Deploy Lambda
python3 module4/deploy_lambda.py
awslocal lambda wait function-active-v2 --function-name ephemeralguard-forensic-trigger

# 3. Attack simulation
python3 module4/iam_attack_sim.py

# 4. Benchmark
python3 module4/benchmark.py
```

---

## IAM Roles Created

| Role | Purpose |
|---|---|
| `ephemeralguard-lambda-role` | Lambda execution role — scoped to S3, DynamoDB, KMS, CloudWatch |
| `ephemeralguard-attacker-role` | Intentionally overprivileged — used as attack target |

---

## Attack Scenarios

| # | Attack | Technique | Severity |
|---|---|---|---|
| 1 | Privilege Escalation | Attach `AdministratorAccess` to Lambda role | CRITICAL |
| 2 | Credential Recon | `sts:GetCallerIdentity` — attacker enumerating identity | HIGH |
| 3 | Unauthorized S3 Access | List forensic dump bucket without permission | CRITICAL |
| 4 | Lateral Movement | `sts:AssumeRole` into attacker role | HIGH |

Every attack is automatically logged to Module 3 (DynamoDB + S3 + KMS).

---

## Lambda Function

`lambda_function.py` is the core forensic trigger. When invoked it:

1. Generates a synthetic memory dump
2. Runs entropy-based page classification (Module 2 triage logic)
3. Builds a Merkle root over kept pages
4. Logs the full forensic event via Module 3

### Invoke manually:

```bash
awslocal lambda invoke \
  --function-name ephemeralguard-forensic-trigger \
  --payload '{"function_name": "fintech-payment-processor", "trigger": "manual_test"}' \
  output.json && cat output.json
```

### Environment variables:

| Variable | Value |
|---|---|
| `AWS_ENDPOINT_URL` | `http://localhost:4566` |
| `BUCKET` | `ephemeralguard-forensics` |
| `TABLE` | `ForensicAuditLog` |
| `KMS_KEY_ID` | Your LocalStack KMS key ID |

---

## Benchmark Results

Tested on LocalStack (Ubuntu VM, i5-12400H, 16GB RAM):

| Metric | Result |
|---|---|
| Min latency | 20.6ms |
| Max latency | 34.0ms |
| Mean latency | 24.0ms |
| Median | 23.6ms |
| Stdev | 2.8ms |
| Target | <100ms |
| Pass rate | 20/20 (100%) ✅ |

> EphemeralGuard meets the **race-against-termination** requirement — forensic events are captured and preserved well within the Lambda termination window.

**Parallel stress test (10 concurrent events):**

| Metric | Result |
|---|---|
| Total wall time | 208ms |
| Mean per event | 152ms |
| Under 100ms | 0/10 |

Note: LocalStack adds serialization overhead under concurrent load. Real AWS KMS/S3 would handle parallel calls with significantly lower latency — this is a simulation environment constraint, not a system design flaw.

---

## Integration with Other Modules

### Depends on:
- **Module 2** — Merkle root + triage stats passed as metadata to `log_audit_event()`
- **Module 3** — `log_audit_event()` and `verify_event()` for all forensic logging

### Module 1 integration (when ready):

When Sinchana's Falco/eBPF detection fires, plug it in like this:

```python
from module3.audit_logger import log_audit_event

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

### Full pipeline (Mod 1 → 2 → 3 → 4):

```
Falco alert (Mod 1)
    → Memory dump + triage + Merkle root (Mod 2)
    → Encrypt + S3 upload + DynamoDB audit log (Mod 3)
    → IAM attack detection + benchmark validation (Mod 4)
```

---

## LocalStack Setup

Prereqs: Docker running.

```bash
cd ~/temp_ccncs-main
docker compose up -d

# Use the repo's setup_localstack.sh instead of bootstrapping by hand —
# it's idempotent, also creates the SQS queue this module needs, and
# writes KMS_KEY_ID / QUEUE_URL / QUEUE_ARN to .env
./setup_localstack.sh
```

`lambda_function.py` and `module3/audit_logger.py` both read `KMS_KEY_ID`
from `.env` automatically now, so there's no manual key update step
needed after a LocalStack reset — just re-run `./setup_localstack.sh` and
redeploy with `python3 deploy_lambda.py`.

---

## Known Issues

| Issue | Cause | Fix |
|---|---|---|
| Lambda stuck in `Pending` | LocalStack pulling runtime container | Run `awslocal lambda wait function-active-v2 --function-name ephemeralguard-forensic-trigger` |
| `NotFoundException` on KMS | Container was reset, key ID changed | Re-run `./setup_localstack.sh` (reuses or recreates the key and updates `.env`), then `python3 deploy_lambda.py` to redeploy with the current key — no manual config edits needed |
| `Float not supported` in DynamoDB | boto3 requires `Decimal` for floats | Convert floats to `str` or `Decimal` before passing as metadata |

---

## Dependencies

```
boto3
awscli
awscli-local
numpy
```

Install: `pip install boto3 numpy awscli awscli-local --break-system-packages`
