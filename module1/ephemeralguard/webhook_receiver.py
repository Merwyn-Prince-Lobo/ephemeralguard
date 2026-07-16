from flask import Flask, request
import json
import os
import boto3

app = Flask(__name__)


def load_queue_url():
    """Read QUEUE_URL written by setup_localstack.sh — same source of
    truth every other module reads from, instead of a hardcoded URL that
    could drift from what actually got created."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
    values = {}
    if not os.path.exists(env_path):
        raise SystemExit(
            f"[✗] {env_path} not found — run ./setup_localstack.sh first."
        )
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line:
                k, v = line.split("=", 1)
                values[k] = v
    if "QUEUE_URL" not in values:
        raise SystemExit("[✗] QUEUE_URL missing from .env — re-run ./setup_localstack.sh")
    return values["QUEUE_URL"]


sqs = boto3.client(
    'sqs',
    endpoint_url='http://localhost:4566',
    region_name='us-east-1',
    aws_access_key_id='test',
    aws_secret_access_key='test'
)

QUEUE_URL = load_queue_url()

@app.route('/falco-alert', methods=['POST'])
def receive_alert():
    alert = request.get_json()
    print(f"\n[ALERT RECEIVED] {json.dumps(alert, indent=2)}")
    if alert.get('priority') in ['Critical', 'CRITICAL', 'Warning', 'WARNING']:
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps({
                'source': 'falco',
                'rule': alert.get('rule'),
                'priority': alert.get('priority'),
                'output': alert.get('output'),
                'function_name': 'fintech-payment-processor',
                'trigger': 'CAPTURE_NOW'
            })
        )
        print("[TRIGGER SENT] Forensic capture triggered via SQS")
    return {"status": "received"}, 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
