import os
import sys
import time
import json
import threading
import boto3

from ring_buffer import start_ring_buffer, get_buffer_contents
from capture import stream_memory_to_s3

sqs = boto3.client(
    'sqs',
    endpoint_url='http://localhost:4566',
    region_name='us-east-1',
    aws_access_key_id='test',
    aws_secret_access_key='test'
)
QUEUE_URL = 'http://localhost:4566/000000000000/forensic-trigger-queue'

def poll_sqs_for_trigger():
    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=5
            )
            messages = response.get('Messages', [])
            for msg in messages:
                body = json.loads(msg['Body'])
                print(f"\n[TRIGGER] Received: {body.get('rule')}")
                print(f"[TRIGGER] Ring buffer has {len(get_buffer_contents())} snapshots")
                key, sha256 = stream_memory_to_s3(
                    trigger_reason=body.get('rule', 'unknown').replace(' ', '_')
                )
                sqs.delete_message(
                    QueueUrl=QUEUE_URL,
                    ReceiptHandle=msg['ReceiptHandle']
                )
                print(f"[TRIGGER] Done. Key: {key} | SHA-256: {sha256}")
        except Exception as e:
            print(f"[SQS POLL ERROR] {e}")
        time.sleep(2)

def main():
    print("[EXTENSION] EphemeralGuard starting...")
    start_ring_buffer()

    t = threading.Thread(target=poll_sqs_for_trigger, daemon=True)
    t.start()
    print("[EXTENSION] Listening for forensic triggers...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[EXTENSION] Shutdown — flushing final capture")
        stream_memory_to_s3(trigger_reason='shutdown_flush')
        sys.exit(0)

if __name__ == "__main__":
    main()
