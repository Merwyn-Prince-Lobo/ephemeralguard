"""
EphemeralGuard - Module 4
IAM roles + policies setup for Lambda deployment and attack simulation
"""

import boto3
import json

ENDPOINT = "http://localhost:4566"
REGION   = "us-east-1"

BOTO_KWARGS = dict(
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)

iam = boto3.client("iam", **BOTO_KWARGS)

TRUST_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"
    }]
})

FORENSIC_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:PutObject", "s3:GetObject"],
            "Resource": "arn:aws:s3:::ephemeralguard-forensics/*"
        },
        {
            "Effect": "Allow",
            "Action": ["dynamodb:PutItem", "dynamodb:GetItem"],
            "Resource": "arn:aws:dynamodb:us-east-1:000000000000:table/ForensicAuditLog"
        },
        {
            "Effect": "Allow",
            "Action": ["kms:Encrypt", "kms:Decrypt"],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": "*"
        }
    ]
})

def setup_iam():
    # Create Lambda execution role
    try:
        iam.create_role(
            RoleName="ephemeralguard-lambda-role",
            AssumeRolePolicyDocument=TRUST_POLICY,
            Description="EphemeralGuard Lambda execution role"
        )
        print("[✓] Created role: ephemeralguard-lambda-role")
    except iam.exceptions.EntityAlreadyExistsException:
        print("[~] Role already exists: ephemeralguard-lambda-role")

    # Attach forensic policy
    try:
        iam.put_role_policy(
            RoleName="ephemeralguard-lambda-role",
            PolicyName="ephemeralguard-forensic-policy",
            PolicyDocument=FORENSIC_POLICY
        )
        print("[✓] Attached forensic policy")
    except Exception as e:
        print(f"[✗] Policy attach failed: {e}")

    # Create attacker role (intentionally overprivileged for simulation)
    try:
        iam.create_role(
            RoleName="ephemeralguard-attacker-role",
            AssumeRolePolicyDocument=TRUST_POLICY,
            Description="Simulated attacker role - overprivileged"
        )
        print("[✓] Created attacker role: ephemeralguard-attacker-role")
    except iam.exceptions.EntityAlreadyExistsException:
        print("[~] Attacker role already exists")

    print("\n[✓] IAM setup complete")

if __name__ == "__main__":
    setup_iam()
