"""
EphemeralGuard - Module 4
Packages and deploys lambda_function.py to LocalStack
"""

import boto3
import zipfile
import io
import os
import json
import subprocess
import tempfile
import shutil

ENDPOINT = "http://localhost:4566"          # used by THIS script, running on the host
LAMBDA_ENDPOINT = "http://172.17.0.1:4566"  # used INSIDE the Lambda sandbox — its own
                                             # container can't reach "localhost:4566",
                                             # that's itself, not LocalStack. 172.17.0.1
                                             # is the Docker bridge back to the host.
REGION   = "us-east-1"
ROLE_ARN = "arn:aws:iam::000000000000:role/ephemeralguard-lambda-role"


def load_env():
    """Read KMS_KEY_ID / QUEUE_URL / QUEUE_ARN written by setup_localstack.sh.
    Run ./setup_localstack.sh from the repo root before this script."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    values = {}
    if not os.path.exists(env_path):
        raise SystemExit(
            f"[✗] {env_path} not found — run ./setup_localstack.sh first "
            "(it creates the bucket/table/queue/KMS key and writes .env)."
        )
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line:
                k, v = line.split("=", 1)
                values[k] = v
    for required in ("KMS_KEY_ID", "QUEUE_ARN"):
        if required not in values:
            raise SystemExit(f"[✗] {required} missing from .env — re-run ./setup_localstack.sh")
    return values


ENV = load_env()
KMS_KEY_ID = ENV["KMS_KEY_ID"]
QUEUE_ARN  = ENV["QUEUE_ARN"]

BOTO_KWARGS = dict(
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)

lam = boto3.client("lambda", **BOTO_KWARGS)


def package_lambda():
    """Zips lambda_function.py together with its numpy dependency.
    boto3 ships preinstalled in the Lambda/LocalStack runtime, but numpy
    does not — without bundling it, `import numpy as np` fails inside the
    sandboxed runtime the moment the function actually invokes."""
    build_dir = tempfile.mkdtemp()
    try:
        subprocess.run(
            [
                "pip", "install", "-t", build_dir, "numpy",
                "--platform", "manylinux2014_x86_64",
                "--python-version", "3.11",
                "--implementation", "cp",
                "--only-binary=:all:",
                "--quiet", "--no-cache-dir",
            ],
            check=True,
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(
                os.path.join(os.path.dirname(__file__), 'lambda_function.py'),
                'lambda_function.py'
            )
            for root, _, files in os.walk(build_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, build_dir)
                    zf.write(full_path, arcname)
        buf.seek(0)
        return buf.read()
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def wire_sqs_trigger():
    """Hook forensic-trigger-queue up as an event source for the Lambda.
    This is the piece that was missing: run_scenario.sh sends a message to
    the queue expecting it to trigger the Lambda, but nothing ever told
    LocalStack the queue and function were connected."""
    existing = lam.list_event_source_mappings(
        FunctionName="ephemeralguard-forensic-trigger",
        EventSourceArn=QUEUE_ARN,
    )["EventSourceMappings"]
    if existing:
        print(f"[~] SQS trigger already wired (UUID: {existing[0]['UUID']})")
        return
    mapping = lam.create_event_source_mapping(
        EventSourceArn=QUEUE_ARN,
        FunctionName="ephemeralguard-forensic-trigger",
        BatchSize=1,
        Enabled=True,
    )
    print(f"[✓] SQS trigger wired: {QUEUE_ARN} -> ephemeralguard-forensic-trigger")


def wait_until_active(timeout=90):
    """LocalStack briefly reports the function as State=Pending right after
    create/update — invoking during that window throws
    ResourceConflictException. Poll until it's Active."""
    import time as _time
    start = _time.time()
    while _time.time() - start < timeout:
        cfg = lam.get_function_configuration(FunctionName="ephemeralguard-forensic-trigger")
        state = cfg.get("State")
        last_update = cfg.get("LastUpdateStatus")
        if state == "Active" and last_update in (None, "Successful"):
            return
        print(f"[~] Waiting for Lambda to become Active (state={state})...")
        _time.sleep(1)
    raise SystemExit("[✗] Lambda never reached Active state in time")


def deploy():
    zip_bytes = package_lambda()

    # Safety net: if a previous run crashed mid-update, the function may
    # already be sitting in Pending/InProgress from that attempt. Wait it
    # out before touching it again, or every call below will collide.
    try:
        lam.get_function_configuration(FunctionName="ephemeralguard-forensic-trigger")
        print("[~] Function already exists — waiting for it to settle first...")
        wait_until_active()
    except lam.exceptions.ResourceNotFoundException:
        pass

    try:
        lam.create_function(
            FunctionName="ephemeralguard-forensic-trigger",
            Runtime="python3.11",
            Role=ROLE_ARN,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=30,
            MemorySize=512,
            Environment={"Variables": {
                "AWS_ENDPOINT_URL": LAMBDA_ENDPOINT,
                "BUCKET":           "ephemeralguard-forensics",
                "TABLE":            "ForensicAuditLog",
                "KMS_KEY_ID":       KMS_KEY_ID,
            }}
        )
        print("[✓] Lambda deployed: ephemeralguard-forensic-trigger")
        wait_until_active()
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(
            FunctionName="ephemeralguard-forensic-trigger",
            ZipFile=zip_bytes,
        )
        wait_until_active()
        lam.update_function_configuration(
            FunctionName="ephemeralguard-forensic-trigger",
            Environment={"Variables": {
                "AWS_ENDPOINT_URL": LAMBDA_ENDPOINT,
                "BUCKET":           "ephemeralguard-forensics",
                "TABLE":            "ForensicAuditLog",
                "KMS_KEY_ID":       KMS_KEY_ID,
            }}
        )
        wait_until_active()
        print("[~] Lambda updated: ephemeralguard-forensic-trigger")

    wire_sqs_trigger()
    wait_until_active()

    response = lam.invoke(
        FunctionName="ephemeralguard-forensic-trigger",
        Payload=json.dumps({
            "function_name": "fintech-payment-processor",
            "trigger":       "deploy_test"
        })
    )
    result = json.loads(response["Payload"].read())
    if response.get("FunctionError"):
        print(f"[✗] Lambda raised an error ({response['FunctionError']}):")
        print(json.dumps(result, indent=2))
        raise SystemExit(1)
    print(f"[✓] Test invoke success")
    print(f"    Event ID   : {result.get('event_id')}")
    print(f"    Capture ms : {result.get('capture_ms')}ms")
    print(f"    Pages kept : {result.get('triage', {}).get('pages_kept')}")


if __name__ == "__main__":
    deploy()
