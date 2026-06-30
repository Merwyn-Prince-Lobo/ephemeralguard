#!/usr/bin/env bash
# EphemeralGuard - LocalStack bootstrap. Creates S3 bucket, DynamoDB table, KMS key. Safe to re-run.
# Usage: docker compose up -d && ./setup_localstack.sh
set -e

BUCKET="ephemeralguard-forensics"
TABLE="ForensicAuditLog"

# wait for LocalStack to respond (capped, won't hang)
echo "Waiting for LocalStack to be reachable..."
ready=false
for i in $(seq 1 30); do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:4566/_localstack/health || echo "000")
  if [ "$status" = "200" ]; then
    ready=true
    break
  fi
  sleep 1
done

if [ "$ready" != "true" ]; then
  echo "LocalStack didn't respond after 30s. Is the container up? (docker ps)"
  exit 1
fi
echo "LocalStack is up."

# S3 bucket
if awslocal s3 ls "s3://${BUCKET}" >/dev/null 2>&1; then
  echo "S3 bucket '${BUCKET}' already exists, skipping."
else
  echo "Creating S3 bucket..."
  awslocal s3 mb "s3://${BUCKET}"
fi

# DynamoDB table
if awslocal dynamodb describe-table --table-name "${TABLE}" >/dev/null 2>&1; then
  echo "DynamoDB table '${TABLE}' already exists, skipping."
else
  echo "Creating DynamoDB table..."
  awslocal dynamodb create-table \
    --table-name "${TABLE}" \
    --attribute-definitions AttributeName=EventId,AttributeType=S \
    --key-schema AttributeName=EventId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST >/dev/null
fi

# KMS key (reuse from .env if still valid)
EXISTING_KEY_ID=""
if [ -f .env ]; then
  EXISTING_KEY_ID=$(grep '^KMS_KEY_ID=' .env | cut -d= -f2)
fi

if [ -n "$EXISTING_KEY_ID" ] && awslocal kms describe-key --key-id "$EXISTING_KEY_ID" >/dev/null 2>&1; then
  echo "Reusing existing KMS key: ${EXISTING_KEY_ID}"
  KMS_KEY_ID="$EXISTING_KEY_ID"
else
  echo "Creating KMS key..."
  KMS_KEY_ID=$(awslocal kms create-key --description "EphemeralGuard Audit Key" \
    --query 'KeyMetadata.KeyId' --output text)
  echo "KMS_KEY_ID=${KMS_KEY_ID}" > .env
fi

echo ""
echo "Done."
echo "  Bucket : ${BUCKET}"
echo "  Table  : ${TABLE}"
echo "  KMS key: ${KMS_KEY_ID}  (saved to .env)"
