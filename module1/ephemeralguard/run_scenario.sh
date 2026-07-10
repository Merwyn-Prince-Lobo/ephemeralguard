#!/bin/bash
echo "============================================"
echo "EphemeralGuard Attack Scenario"
echo "============================================"
echo ""
echo "Starting normal payment traffic in background..."
python3 ~/ephemeralguard/normal_traffic.py &
NORMAL_PID=$!
echo "Normal traffic PID: $NORMAL_PID"

sleep 3

echo ""
echo "Injecting attack simulation..."
python3 ~/ephemeralguard/attack_simulation.py &
ATTACK_PID=$!
echo "Attack PID: $ATTACK_PID"

sleep 5

echo ""
echo "Sending forensic trigger to SQS..."
awslocal sqs send-message \
  --queue-url http://localhost:4566/000000000000/forensic-trigger-queue \
  --message-body '{
    "source": "falco",
    "rule": "Cryptomining_process_detected",
    "priority": "CRITICAL",
    "function_name": "payment-processor",
    "trigger": "CAPTURE_NOW"
  }'

echo "Trigger sent — waiting for capture to complete..."
sleep 15

echo ""
echo "Checking S3 for captured dump..."
awslocal s3 ls s3://forensic-evidence-bucket/dumps/

echo ""
echo "Cleaning up..."
kill $NORMAL_PID $ATTACK_PID 2>/dev/null
echo "Done."
