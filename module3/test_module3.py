from audit_logger import log_audit_event, verify_event

print("=" * 55)
print("  EphemeralGuard Module 3 - Audit Logger Test")
print("=" * 55)

event_id = log_audit_event(
    event_type="MEMORY_DUMP",
    function_name="fintech-payment-processor",
    metadata={
        "trigger":        "anomaly_detected",
        "memory_size_mb": 512,
        "runtime":        "python3.11",
    }
)

print()

event_id_2 = log_audit_event(
    event_type="IAM_POLICY_CHANGE",
    function_name="ephemeralguard-monitor",
    metadata={
        "action":   "AttachRolePolicy",
        "role":     "lambda-execution-role",
        "severity": "CRITICAL",
    }
)

print()
print("── Tamper Verification ──────────────────────────────")
verify_event(event_id)
verify_event(event_id_2)
