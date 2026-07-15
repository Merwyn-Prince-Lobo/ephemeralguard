#!/bin/bash
set -e

echo "============================================"
echo "EphemeralGuard Attack Scenario"
echo "============================================"

# Locate repo root (.env lives here, written by setup_localstack.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "[✗] ${ENV_FILE} not found. Run ./setup_localstack.sh from the repo root first."
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

if [ -z "$QUEUE_URL" ]; then
  echo "[✗] QUEUE_URL missing from .env — re-run ./setup_localstack.sh"
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q '^victim$'; then
  echo "[✗] 'victim' container isn't running. Run: cd falco && docker compose up -d"
  exit 1
fi

echo ""
echo "Starting normal payment traffic inside victim container..."
docker exec -d victim python3 /ephemeralguard/normal_traffic.py

sleep 3

echo ""
echo "Injecting attack simulation inside victim container..."
docker exec -d victim python3 /ephemeralguard/attack_simulation.py

sleep 5

echo ""
echo "Sending forensic trigger to SQS (${QUEUE_URL})..."
awslocal sqs send-message \
  --queue-url "${QUEUE_URL}" \
  --message-body '{
    "source": "falco",
    "rule": "Cryptomining_process_detected",
    "priority": "CRITICAL",
    "function_name": "fintech-payment-processor",
    "trigger": "CAPTURE_NOW"
  }'

echo "Trigger sent — the SQS event source mapping should invoke"
echo "ephemeralguard-forensic-trigger automatically. Waiting for capture..."
sleep 15

echo ""
echo "Checking S3 for captured dump..."
awslocal s3 ls s3://ephemeralguard-forensics/dumps/ --recursive

echo ""
echo "Cleaning up processes inside victim container..."
docker exec victim pkill -f normal_traffic.py 2>/dev/null || true
docker exec victim pkill -f attack_simulation.py 2>/dev/null || true
echo "Done."