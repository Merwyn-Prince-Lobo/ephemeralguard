"""
EphemeralGuard - Module 4
Packages and deploys lambda_function.py to LocalStack
"""

import boto3
import zipfile
import io
import os
import json

ENDPOINT = "http://localhost:4566"
REGION   = "us-east-1"
ROLE_ARN = "arn:aws:iam::000000000000:role/ephemeralguard-lambda-role"

BOTO_KWARGS = dict(
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)

lam = boto3.client("lambda", **BOTO_KWARGS)


def package_lambda():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(
            os.path.join(os.path.dirname(__file__), 'lambda_function.py'),
            'lambda_function.py'
        )
    buf.seek(0)
    return buf.read()


def deploy():
    zip_bytes = package_lambda()

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
                "AWS_ENDPOINT_URL": ENDPOINT,
                "BUCKET":           "ephemeralguard-forensics",
                "TABLE":            "ForensicAuditLog",
                "KMS_KEY_ID":       "47237a51-4083-4834-81de-2044bbfada20",
            }}
        )
        print("[✓] Lambda deployed: ephemeralguard-forensic-trigger")
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(
            FunctionName="ephemeralguard-forensic-trigger",
            ZipFile=zip_bytes,
        )
        print("[~] Lambda updated: ephemeralguard-forensic-trigger")

    response = lam.invoke(
        FunctionName="ephemeralguard-forensic-trigger",
        Payload=json.dumps({
            "function_name": "fintech-payment-processor",
            "trigger":       "deploy_test"
        })
    )
    result = json.loads(response["Payload"].read())
    print(f"[✓] Test invoke success")
    print(f"    Event ID   : {result.get('event_id')}")
    print(f"    Capture ms : {result.get('capture_ms')}ms")
    print(f"    Pages kept : {result.get('triage', {}).get('pages_kept')}")


if __name__ == "__main__":
    deploy()
