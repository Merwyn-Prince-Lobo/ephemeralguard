import hashlib
import time
import re
import boto3
from botocore.config import Config

CHUNK_SIZE = 10 * 1024 * 1024  # 10MB

s3 = boto3.client(
    's3',
    endpoint_url='http://172.17.0.1:4566',
    region_name='us-east-1',
    aws_access_key_id='test',
    aws_secret_access_key='test',
    # Newer boto3/botocore versions default to auto-adding a CRC32
    # checksum on every S3 request. LocalStack's multipart upload
    # handling doesn't always agree with that default, causing
    # "Checksum Type mismatch" errors. Forcing "when_required" restores
    # the old behavior (only send a checksum if the API call requires one).
    config=Config(
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    ),
)

BUCKET = 'ephemeralguard-forensics'

MAPS_LINE_RE = re.compile(
    r'^([0-9a-f]+)-([0-9a-f]+)\s+([rwxps-]{4})'
)


def get_readable_regions(pid='self'):
    """
    Parse /proc/<pid>/maps and return a list of (start, end) byte-offset
    tuples for every region marked readable ('r'). This is required
    because /proc/<pid>/mem cannot be read as a flat linear file — most
    of the address space is unmapped, and any read there raises
    OSError (Errno 5, I/O error) without advancing the file position.
    We must seek() to each mapped region's start before reading it.
    """
    regions = []
    with open(f'/proc/{pid}/maps', 'r') as f:
        for line in f:
            m = MAPS_LINE_RE.match(line)
            if not m:
                continue
            start_hex, end_hex, perms = m.groups()
            if perms[0] != 'r':
                continue
            regions.append((int(start_hex, 16), int(end_hex, 16)))
    return regions


def stream_memory_to_s3(trigger_reason='manual', pid='self'):
    key = f"dumps/{int(time.time())}_{trigger_reason}.dump"
    print(f"[CAPTURE] Starting memory dump → s3://{BUCKET}/{key}")

    try:
        regions = get_readable_regions(pid)
        print(f"[CAPTURE] Found {len(regions)} readable regions in /proc/{pid}/maps")

        upload = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
        upload_id = upload['UploadId']
        parts = []
        part_number = 1
        sha256 = hashlib.sha256()
        buffer = bytearray()
        regions_skipped = 0

        with open(f'/proc/{pid}/mem', 'rb', buffering=0) as mem_file:
            for start, end in regions:
                region_size = end - start
                try:
                    mem_file.seek(start)
                except OSError:
                    regions_skipped += 1
                    continue

                remaining = region_size
                while remaining > 0:
                    read_size = min(CHUNK_SIZE - len(buffer), remaining)
                    try:
                        data = mem_file.read(read_size)
                    except OSError:
                        # Region listed in maps but not actually readable
                        # right now (e.g. swapped out, race with the
                        # target process). Skip the rest of this region,
                        # not the whole capture.
                        regions_skipped += 1
                        break

                    if not data:
                        break

                    buffer.extend(data)
                    remaining -= len(data)

                    if len(buffer) >= CHUNK_SIZE:
                        chunk = bytes(buffer)
                        sha256.update(chunk)
                        response = s3.upload_part(
                            Bucket=BUCKET, Key=key, PartNumber=part_number,
                            UploadId=upload_id, Body=chunk,
                        )
                        parts.append({'PartNumber': part_number, 'ETag': response['ETag']})
                        print(f"[CAPTURE] Chunk {part_number} uploaded ({len(chunk)} bytes)")
                        part_number += 1
                        buffer = bytearray()

        # Flush whatever's left in the buffer as the final part
        if buffer:
            chunk = bytes(buffer)
            sha256.update(chunk)
            response = s3.upload_part(
                Bucket=BUCKET, Key=key, PartNumber=part_number,
                UploadId=upload_id, Body=chunk,
            )
            parts.append({'PartNumber': part_number, 'ETag': response['ETag']})
            print(f"[CAPTURE] Final chunk {part_number} uploaded ({len(chunk)} bytes)")

        if regions_skipped:
            print(f"[CAPTURE] Note: {regions_skipped} regions were unreadable and skipped")

        if parts:
            s3.complete_multipart_upload(
                Bucket=BUCKET, Key=key, UploadId=upload_id,
                MultipartUpload={'Parts': parts},
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