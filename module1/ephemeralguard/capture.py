import hashlib
import time
import boto3

CHUNK_SIZE = 10 * 1024 * 1024  # 10MB

s3 = boto3.client(
    's3',
    endpoint_url='http://localhost:4566',
    region_name='us-east-1',
    aws_access_key_id='test',
    aws_secret_access_key='test'
)

BUCKET = 'forensic-evidence-bucket'

def stream_memory_to_s3(trigger_reason='manual'):
    key = f"dumps/{int(time.time())}_{trigger_reason}.dump"
    print(f"[CAPTURE] Starting memory dump → s3://{BUCKET}/{key}")

    try:
        upload = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
        upload_id = upload['UploadId']
        parts = []
        part_number = 1
        sha256 = hashlib.sha256()

        with open('/proc/self/mem', 'rb', buffering=0) as mem_file:
            while True:
                try:
                    chunk = mem_file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    sha256.update(chunk)
                    response = s3.upload_part(
                        Bucket=BUCKET,
                        Key=key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=chunk
                    )
                    parts.append({
                        'PartNumber': part_number,
                        'ETag': response['ETag']
                    })
                    print(f"[CAPTURE] Chunk {part_number} uploaded ({len(chunk)} bytes)")
                    del chunk
                    part_number += 1
                except OSError:
                    continue

        if parts:
            s3.complete_multipart_upload(
                Bucket=BUCKET,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )
            final_hash = sha256.hexdigest()
            print(f"[CAPTURE] Complete. SHA-256: {final_hash}")
            print(f"[CAPTURE] Stored at s3://{BUCKET}/{key}")
            return key, final_hash
        else:
            s3.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=upload_id)
            print("[CAPTURE] No readable chunks — aborted")
            return None, None

    except Exception as e:
        print(f"[CAPTURE ERROR] {e}")
        return None, None
