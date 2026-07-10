from flask import Flask, request
import json
import boto3

app = Flask(__name__)

sqs = boto3.client(
    'sqs',
    endpoint_url='http://localhost:4566',
    region_name='us-east-1',
    aws_access_key_id='test',
    aws_secret_access_key='test'
)

QUEUE_URL = 'http://localhost:4566/000000000000/forensic-trigger-queue'

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
                'function_name': 'payment-processor',
                'trigger': 'CAPTURE_NOW'
            })
        )
        print("[TRIGGER SENT] Forensic capture triggered via SQS")
    return {"status": "received"}, 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
