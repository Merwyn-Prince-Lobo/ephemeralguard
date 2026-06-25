"""
EphemeralGuard - Module 4
IAM Attack Simulation — simulates real fintech attack scenarios
Each attack is logged via Module 3
"""

import sys
import boto3
sys.path.append('/home/ubuntu/temp_ccncs/module3')
from audit_logger import log_audit_event

ENDPOINT = "http://localhost:4566"
REGION   = "us-east-1"

BOTO_KWARGS = dict(
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)

iam = boto3.client("iam", **BOTO_KWARGS)
sts = boto3.client("sts", **BOTO_KWARGS)
s3  = boto3.client("s3",  **BOTO_KWARGS)


def attack_privilege_escalation():
    print("\n[ATTACK 1] Privilege Escalation")
    try:
        iam.attach_role_policy(
            RoleName="ephemeralguard-lambda-role",
            PolicyArn="arn:aws:iam::aws:policy/AdministratorAccess"
        )
        result = "SUCCESS — AdminAccess attached to lambda role"
        print(f"  [!] {result}")
    except Exception as e:
        result = f"BLOCKED — {e}"
        print(f"  [~] {result}")

    log_audit_event(
        event_type="IAM_ATTACK_PRIVILEGE_ESCALATION",
        function_name="ephemeralguard-attacker",
        metadata={
            "attack":   "AttachRolePolicy",
            "target":   "ephemeralguard-lambda-role",
            "policy":   "AdministratorAccess",
            "severity": "CRITICAL",
            "result":   result,
        }
    )


def attack_credential_recon():
    print("\n[ATTACK 2] Credential Recon")
    try:
        identity = sts.get_caller_identity()
        result   = f"SUCCESS — Account: {identity['Account']}, ARN: {identity['Arn']}"
        print(f"  [!] {result}")
    except Exception as e:
        result = f"BLOCKED — {e}"
        print(f"  [~] {result}")

    log_audit_event(
        event_type="IAM_ATTACK_CREDENTIAL_RECON",
        function_name="ephemeralguard-attacker",
        metadata={
            "attack":   "sts:GetCallerIdentity",
            "severity": "HIGH",
            "result":   result,
        }
    )


def attack_unauthorized_s3_access():
    print("\n[ATTACK 3] Unauthorized S3 Access")
    try:
        objects = s3.list_objects_v2(Bucket="ephemeralguard-forensics")
        count   = objects.get("KeyCount", 0)
        result  = f"SUCCESS — Listed {count} forensic dump(s) in bucket"
        print(f"  [!] {result}")
    except Exception as e:
        result = f"BLOCKED — {e}"
        print(f"  [~] {result}")

    log_audit_event(
        event_type="IAM_ATTACK_UNAUTHORIZED_S3",
        function_name="ephemeralguard-attacker",
        metadata={
            "attack":   "s3:ListObjects on forensic bucket",
            "bucket":   "ephemeralguard-forensics",
            "severity": "CRITICAL",
            "result":   result,
        }
    )


def attack_lateral_movement():
    print("\n[ATTACK 4] Lateral Movement")
    try:
        response = sts.assume_role(
            RoleArn="arn:aws:iam::000000000000:role/ephemeralguard-attacker-role",
            RoleSessionName="attacker-session"
        )
        result = f"SUCCESS — Assumed role: {response['AssumedRoleUser']['Arn']}"
        print(f"  [!] {result}")
    except Exception as e:
        result = f"BLOCKED — {e}"
        print(f"  [~] {result}")

    log_audit_event(
        event_type="IAM_ATTACK_LATERAL_MOVEMENT",
        function_name="ephemeralguard-attacker",
        metadata={
            "attack":      "sts:AssumeRole",
            "target_role": "ephemeralguard-attacker-role",
            "severity":    "HIGH",
            "result":      result,
        }
    )


if __name__ == "__main__":
    print("=" * 55)
    print("  EphemeralGuard Module 4 - IAM Attack Simulation")
    print("=" * 55)

    attack_privilege_escalation()
    attack_credential_recon()
    attack_unauthorized_s3_access()
    attack_lateral_movement()

    print("\n[✓] All attacks simulated and logged to Module 3")
