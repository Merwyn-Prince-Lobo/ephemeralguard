# Falco Runtime Security Stack — Status & Setup

## Overview

This stack runs Falco (with Falcosidekick + UI) to detect suspicious
runtime behavior inside containers, using a mix of built-in Falco rules
and two custom rule files (`custom-rules.yaml`, `ephemeralguard-rules.yaml`).

Tested on: Ubuntu 24.04, kernel 6.17, Docker Compose, AppArmor enabled.

---

## ✅ Currently Working — everything below is confirmed live, end to end

- Falco starts cleanly and loads the modern eBPF driver (no kernel module
  needed).
- All rule files load and validate with no schema errors.
- Falcosidekick + Falcosidekick UI receive and display alerts live at
  `http://<host>:2802`.
- Custom rule **`Secrets File Read`** (Critical) — fires when any process
  reads a file ending in `secrets.txt` inside a container.
- Custom rule **`Suspicious Outbound Tool Used`** (Warning) — fires when
  `curl` or `wget` runs inside the `victim` container.
- Custom rule **`EphemeralGuard - Cryptomining Process Pattern`**
  (Critical) — **confirmed firing live** on `attack_simulation.py`
  (see Session 2 fixes below; this rule silently never loaded until now).
- Custom rule **`EphemeralGuard - Outbound to Known C2 Range`** (Critical)
  — **confirmed firing live** on the real outbound socket connect to
  `185.220.101.47:443`.
- LocalStack (`ephemeralguard-localstack`) starts and reports healthy,
  independent of the Falco stack.
- The **full `module1/ephemeralguard/run_scenario.sh` attack scenario**
  now runs end to end: normal traffic + attack simulation injected into
  `victim` → SQS trigger → Lambda invoke → Module 2 triage → Module 3
  encrypt/upload → forensic dump lands in S3 → both EphemeralGuard rules
  fire on the live behavior, with no false positives from the cleanup
  step.
- Module 2's triage → chunking → Merkle root pipeline runs correctly in
  isolation (verified with a synthetic memory dump, no AWS dependency).

---

## 🔧 Fixes Applied to `falco/docker-compose.yaml`

The original compose file had several issues that prevented Falco from
running reliably on Ubuntu:

1. **AppArmor was blocking Falco's privileged operations.**
   Added:
```yaml
   security_opt:
     - apparmor:unconfined
```

2. **Missing tracing mount for the modern eBPF driver.**
   Added:
```yaml
   - /sys/kernel/tracing:/sys/kernel/tracing:ro
```
   (kept the existing `/sys/kernel/debug` mount too, for compatibility)

3. **Duplicate volume mounts and duplicate environment variables**
   (`ephemeralguard-rules.yaml` was mounted twice; `WEBHOOK_ADDRESS` was
   set twice) — cleaned up, no functional impact but was sloppy.

4. **Custom rules were being silently shadowed by default Falco rules.**
   Falco's default behavior evaluates rules per event type and stops at
   the *first* match (`rule_matching: first`). Since curl execve events
   also match the built-in `Drop and execute new binary in container`
   rule, our custom `Suspicious Outbound Tool Used` rule never got a
   chance to fire. Fixed by adding a command-line override so **all**
   matching rules fire per event:
```yaml
   command: >
     /usr/bin/falco
     -o rule_matching=all
     -o json_output=true
     -o http_output.enabled=true
     -o http_output.url=http://falcosidekick:2801/
```
   Note: this increases alert volume, by design — every rule that
   matches an event will now report, not just the first one.

---

## 🚀 How to Run

Two independent Docker Compose stacks make up this project:

```bash
# Stack 1: LocalStack (fake AWS), from repo root
cd ~/temp_ccncs
docker compose up -d

# Stack 2: Falco + Falcosidekick + victim container
cd ~/temp_ccncs/falco
docker compose up -d
```

Verify everything is up:
```bash
docker ps -a
```
You should see: `falco`, `falcosidekick`, `falcosidekick-ui`,
`falcosidekick-ui-redis`, `victim`, and `ephemeralguard-localstack`
(the last one takes ~10-15s to report `healthy`).

---

## 🧪 How to Test

**Custom Falco rules:**
```bash
# Triggers "Secrets File Read"
docker exec victim sh -c "echo topsecret > /tmp/secrets.txt && cat /tmp/secrets.txt"

# Triggers "Suspicious Outbound Tool Used" (and also the built-in
# "Drop and execute new binary" rule, since curl isn't part of the base
# alpine image)
docker exec victim curl -s http://example.com
```

Watch alerts either via:
```bash
docker compose logs -f falco
```
or the Falcosidekick UI at `http://<host>:2802`.

**LocalStack:**
```bash
cd ~/temp_ccncs
./setup_localstack.sh
```
This creates an S3 bucket (`ephemeralguard-forensics`), a DynamoDB table
(`ForensicAuditLog`), and a KMS key — safe to re-run.

**Full attack scenario (Falco + LocalStack + Lambda, all confirmed working):**
```bash
cd ~/temp_ccncs-main
docker compose up -d
./setup_localstack.sh
cd module4 && python3 iam_setup.py && python3 deploy_lambda.py
cd ../falco && docker compose up -d
cd ../module1/ephemeralguard && ./run_scenario.sh
```
Then check:
```bash
docker compose -f falco/docker-compose.yaml logs falco --since 1m | grep -i "EphemeralGuard -"
```
Expect exactly two Critical alerts per run — `Cryptomining Process
Pattern` and `Outbound to Known C2 Range` — and a new `.enc` forensic
dump under `s3://ephemeralguard-forensics/dumps/`.

---

## 📋 Remaining Known Issues

1. **`module4/iam_attack_sim.py` hardcodes a path** —
   `sys.path.append('/home/ubuntu/temp_ccncs/module3')` — and will break
   unless your repo happens to live at that exact path. Not yet fixed.

2. **AWS CLI / `awslocal` setup** should be confirmed on any new host —
   needed for `run_scenario.sh` and `setup_localstack.sh`.

3. Consider removing the `version: "3.8"` line from the root
   `docker-compose.yml` if it's still there — obsolete in current Docker
   Compose, produces a harmless warning on every run.

4. Benchmark numbers in `module4/readme_mod4.md` (latency, parallel
   stress test) have not been re-verified since the fixes below — worth
   re-running `module4/benchmark.py` if those numbers matter for your
   writeup.

---

## Session 1 — EphemeralGuard Fixes Applied

### 1. `docker-compose.yml` (root)
- Added `sqs` to `SERVICES=` — LocalStack was never running SQS at all, so
  the queue couldn't be created regardless of what the scripts said.
- Dropped the obsolete `version: "3.8"` key.

### 2. `setup_localstack.sh`
- Added SQS queue creation (`forensic-trigger-queue`), idempotent like the
  rest of the script.
- Now writes **all three** values to `.env` every run: `KMS_KEY_ID`,
  `QUEUE_URL`, `QUEUE_ARN`. Previously only the KMS key was saved, and only
  on first creation — this is now the single source of truth other scripts
  read from.

### 3. `module4/iam_setup.py`
- Added `sqs:ReceiveMessage` / `sqs:DeleteMessage` / `sqs:GetQueueAttributes`
  to the Lambda role's policy. Without this, even a correctly-wired event
  source mapping can't poll the queue.

### 4. `module4/deploy_lambda.py`
- No longer hardcodes a KMS key ID that didn't match what
  `setup_localstack.sh` actually created — now reads `KMS_KEY_ID` from
  `.env`. This was a silent time-bomb: `kms.encrypt()` would have failed
  the first time the Lambda actually ran for real.
- Added `wire_sqs_trigger()` — creates the SQS→Lambda event source mapping
  that never existed anywhere in the repo. This is *the* missing link:
  previously, sending a message to the queue did nothing at all.
- `update_function_configuration` now also runs on redeploy, so the env
  vars stay in sync if you re-run this after the KMS key rotates.

### 5. `module4/lambda_function.py`
- `lambda_handler` now unwraps SQS-triggered events (`event["Records"][0]
  ["body"]`, JSON-encoded) in addition to direct-invoke events. Before this,
  the function only worked when called directly via `lam.invoke()` (as
  `deploy_lambda.py`'s test call does) — the real SQS-triggered path would
  have silently returned `function_name: "unknown-lambda"`.

### 6. `falco/docker-compose.yaml`
- `victim` container was bare `alpine:latest` running `sleep infinity` —
  no Python, nothing mounted. The attack scenario could never physically
  run inside it, so Falco was watching a container that never did anything.
- Now uses `python:3.11-slim`, installs `boto3`/`numpy` on start, and mounts
  `module1/ephemeralguard/` read-only at `/ephemeralguard`.

### 7. `module1/ephemeralguard/attack_simulation.py`
- Added a real `socket.connect()` attempt to the C2 IP (`185.220.101.47`,
  port 443, 3s timeout, wrapped in try/except — expected to fail/timeout,
  that's fine and by design). Previously the IP only existed as a string in
  a dict; no actual network syscall was ever made, so the "Outbound to
  Known C2 Range" Falco rule (which matches on `fd.sip`) was **unfireable**
  no matter how many times you ran the script.

### 8. `module1/ephemeralguard/run_scenario.sh`
- Rewritten to run `normal_traffic.py` / `attack_simulation.py` via
  `docker exec -d victim python3 /ephemeralguard/...` instead of directly
  on the host — this is what actually puts the process inside the
  container Falco is monitoring.
- Reads `QUEUE_URL` from `.env` instead of a hardcoded URL.
- Fixed the S3 bucket check to `ephemeralguard-forensics` (was
  `forensic-evidence-bucket`, which never existed).
- Checks that `victim` is actually running before doing anything, and that
  `.env` exists (i.e. `setup_localstack.sh` was run first).

---

## Session 2 — Bugs found and fixed during live testing

Everything in Session 1 got the stack *running*. This pass actually
exercised it end to end (LocalStack → Lambda → SQS → Falco) and found
real bugs that static checks alone had missed.

### 9. `module2/pipeline.py` — deleted
Byte-for-byte identical duplicate of `module2/secure_transfer.py`. Nothing
in the repo imported it by name; `secure_transfer.py` is the one
referenced everywhere (`chunk_streamer.py`'s own docstring points to it).
Dead file, removed.

### 10. `module2/fakemen_dump.py`
- Docstring claimed a `40% zero / 30% low-entropy / 20% high-entropy /
  10% structured` distribution. The actual `if/elif` thresholds produce
  `10% / 30% / 45% / 15%`. Docstring corrected to match the code.
- The module ran `generate_synthetic_dump(...)` at import time — anyone
  who just did `import fakemen_dump` got a silent 1GB file written to
  disk. Wrapped in `if __name__ == "__main__":`.

### 11. `module2/readme_mod2.md`
- Stated expected triage result was ~70% dropped / 30% retained. A real
  run against the corrected generator showed ~40% dropped / 60% retained
  with the current classifier thresholds. Updated to match reality.

### 12. `module3/audit_logger.py`
- Hardcoded `KMS_KEY_ID = "28c7c3c0-..."` directly in the file, with no
  way to pick up a rotated key. `module4/deploy_lambda.py` already reads
  `KMS_KEY_ID` from `.env` (Session 1, #4) — `audit_logger.py` was never
  brought in line with that fix, so standalone testing via
  `test_module3.py` would silently use a stale key after any LocalStack
  reset. Now reads `KMS_KEY_ID` from `.env` the same way, and fails loudly
  with a clear message if `.env` is missing instead of using a bad key.

### 13. `module1/ephemeralguard/run_scenario.sh` — `pkill` not available
- Cleanup used `docker exec victim pkill -f ...`, but the `victim`
  container runs `python:3.11-slim`, which has no `procps` package (no
  `pkill` binary). Every run ended with two
  `OCI runtime exec failed: ... "pkill": executable file not found`
  errors (non-fatal, but noisy). Replaced with a `/proc`-scanning kill —
  see fix #14.

### 14. `falco/rules/ephemeralguard-rules.yaml` — broken YAML, biggest find
- The `condition: >` folded block scalar for the cryptomining rule had
  inconsistent indentation: its first content line (`spawned_process and
  (`) had 5 leading spaces, while the following lines had only 4 — less
  than the block's established indent. This is invalid YAML.
- **Falco was silently rejecting this entire rules file on every single
  load** (`schema validation: none` / `Invalid`, logged roughly every
  30-60 seconds since the stack came up). Both EphemeralGuard rules were
  never loaded at all — not "loads fine, never triggered" as originally
  believed. Fixed the indentation; Falco now logs
  `schema validation: ok` and both rules fire correctly.
- Note: a first attempt at validating this file with PyYAML incorrectly
  reported it as fine, because the validation script called
  `yaml.safe_load_all()` without actually iterating the returned
  generator, so no parsing ever happened. Re-validated properly
  (`list(yaml.safe_load_all(...))`) and the error reproduced immediately.
  Worth remembering for any future YAML checks in this repo.

### 15. New file: `module1/ephemeralguard/cleanup.py`
- The `/proc`-scanning cleanup logic from fix #13 was originally passed
  inline via `docker exec victim python3 -c "..."`. Because the inline
  code contained the literal string `attack_simulation.py` (as one of its
  own target filenames), that string showed up in the cleanup process's
  own `proc.cmdline` — which is exactly what the
  `EphemeralGuard - Cryptomining Process Pattern` rule matches on. Every
  cleanup step was self-triggering a false Critical alert.
- Moved the logic into `cleanup.py` (mounted read-only into `victim`
  alongside the other scripts) and call it as
  `docker exec victim python3 /ephemeralguard/cleanup.py`. The process's
  cmdline is now just a file path, so it no longer contains the trigger
  string. Confirmed with a live run: exactly 2 real alerts per scenario,
  0 false positives from cleanup.

---

## Run order (matters)

```bash
cd ~/temp_ccncs-main
docker compose up -d              # LocalStack (with SQS enabled)
./setup_localstack.sh             # bucket + table + queue + KMS key -> .env

cd module4
python3 iam_setup.py              # role + policies (incl. SQS perms)
python3 deploy_lambda.py          # deploys Lambda + wires SQS trigger + test invoke

cd ../falco
docker compose up -d              # Falco + victim (python-capable, mounted)

cd ../module1/ephemeralguard
./run_scenario.sh                 # the actual end-to-end attack scenario — confirmed working
```
