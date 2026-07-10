# Falco Runtime Security Stack — Status & Setup

## Overview

This stack runs Falco (with Falcosidekick + UI) to detect suspicious
runtime behavior inside containers, using a mix of built-in Falco rules
and two custom rule files (`custom-rules.yaml`, `ephemeralguard-rules.yaml`).

Tested on: Ubuntu 24.04, kernel 6.17, Docker Compose, AppArmor enabled.

---

## ✅ Currently Working

- Falco starts cleanly and loads the modern eBPF driver (no kernel module
  needed).
- All rule files load and validate with no schema errors.
- Falcosidekick + Falcosidekick UI receive and display alerts live at
  `http://<host>:2802`.
- Custom rule **`Secrets File Read`** (Critical) — fires when any process
  reads a file ending in `secrets.txt` inside a container.
- Custom rule **`Suspicious Outbound Tool Used`** (Warning) — fires when
  `curl` or `wget` runs inside the `victim` container.
- LocalStack (`ephemeralguard-localstack`) starts and reports healthy,
  independent of the Falco stack.

---

## ⚠️ Not Yet Tested / Verified

- `EphemeralGuard - Cryptomining Process Pattern` rule — loads fine, never
  triggered live.
- `EphemeralGuard - Outbound to Known C2 Range` rule — loads fine, never
  triggered live.
- The full `module1/ephemeralguard/run_scenario.sh` attack simulation —
  **known to be broken as-is** (see Pending Work below).

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

---

## 📋 Pending Work

1. **`module1/ephemeralguard/run_scenario.sh` is broken** — it references
   AWS resources that are never created:
   - SQS queue `forensic-trigger-queue` — not created anywhere
   - S3 bucket `forensic-evidence-bucket` — doesn't match the bucket
     `setup_localstack.sh` actually creates (`ephemeralguard-forensics`)
   - Lambda function `payment-processor` — never deployed by this script
     (that's handled separately by `module4/deploy_lambda.py`, but the
     two aren't wired together yet)

   To fix, need to either:
   - Update `run_scenario.sh` to use the bucket name `setup_localstack.sh`
     actually creates, **and**
   - Add SQS queue creation to `setup_localstack.sh` (or a new script),
     **and**
   - Run `module4/deploy_lambda.py` before the scenario, and confirm the
     function name matches (`payment-processor`)

2. **Fire the two EphemeralGuard rules live** to confirm they actually
   work, not just load:
   - Cryptomining rule: run `module1/ephemeralguard/attack_simulation.py`
     inside a container and confirm the alert fires.
   - C2 rule: simulate an outbound connection to `185.220.101.47` and
     confirm the alert fires (careful — this is a real IP; test inside
     an isolated network only, don't actually route real traffic to it).

3. **`awslocal` / AWS CLI setup** — not yet confirmed installed/configured
   on the host. Needed for `run_scenario.sh` and `setup_localstack.sh` to
   work.

4. Consider removing the `version: "3.8"` line from the root
   `docker-compose.yml` — it's obsolete in current Docker Compose and
   just produces a harmless warning on every run.
