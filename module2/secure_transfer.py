"""
SECURE TRANSFER MODULE FOR FORENSIC EVIDENCE PIPELINE

Responsible ONLY for:
    * Taking completed Chunk objects from chunk_streamer.py and uploading
      them to S3 via multipart upload.
    * Retrying failed part uploads (up to 3 attempts, exponential backoff,
      logging every retry).
    * Aborting the multipart upload and cleaning up if a chunk ultimately
      fails after all retries.
    * Tracking actual uploaded bytes and computing real throughput
      (uploaded_bytes / elapsed_time), instead of assuming every chunk is
      exactly the configured chunk size.

This module is the only place that talks to S3/boto3. It depends on
chunk_streamer.Chunk but chunk_streamer.py never imports anything from here.
"""

import logging
import time
from typing import Dict, List, Optional

from chunk_streamer import Chunk

logger = logging.getLogger("secure_transfer")

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0


class MultipartUploadError(Exception):
    """Raised when a chunk upload fails after all retries are exhausted."""


class SecureTransferModule:
    """
    Uploads Chunk objects produced by ChunkStreamer to S3 using a multipart
    upload, with per-part retry/backoff and abort-on-failure semantics.
    """

    def __init__(self, s3_client, bucket: str, key: str, case_id: str):
        self.s3_client = s3_client
        self.bucket = bucket
        self.key = key
        self.case_id = case_id

        self.upload_id: Optional[str] = None
        self.parts: List[Dict] = []
        self.uploaded_bytes = 0
        self.start_time: Optional[float] = None

    def start(self) -> None:
        """Initiate the multipart upload."""
        mpu = self.s3_client.create_multipart_upload(
            Bucket=self.bucket,
            Key=self.key,
            Metadata={
                'case-id': self.case_id,
                'method': 'chunk-streaming',
                'triage-mode': 'entropy-filtered',
            },
        )
        self.upload_id = mpu['UploadId']
        self.start_time = time.perf_counter()

    def upload_chunk(self, chunk: Chunk) -> None:
        """
        Upload a single completed chunk, retrying on failure. If all
        retries fail, abort the multipart upload, clean up, and raise.
        """
        if self.upload_id is None:
            raise RuntimeError("Call start() before uploading chunks")

        part_number = chunk.chunk_id + 1  # PartNumber is 1-indexed
        try:
            etag = self._upload_part_with_retry(part_number, chunk.data)
        except Exception as exc:
            self._abort(reason=f"chunk {chunk.chunk_id} failed: {exc}")
            raise MultipartUploadError(
                f"Chunk {chunk.chunk_id} failed after {MAX_RETRIES} retries; "
                f"multipart upload aborted and cleaned up"
            ) from exc

        self.parts.append({'PartNumber': part_number, 'ETag': etag})
        self.uploaded_bytes += len(chunk.data)

    def complete(self) -> Dict:
        """Complete the multipart upload and return upload statistics."""
        if not self.parts:
            raise MultipartUploadError("No parts uploaded; nothing to complete")

        self.s3_client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=self.key,
            UploadId=self.upload_id,
            MultipartUpload={'Parts': sorted(self.parts, key=lambda p: p['PartNumber'])},
        )

        elapsed = time.perf_counter() - self.start_time
        # Real throughput, based on actual uploaded bytes - not an assumed
        # fixed chunk size, since the final chunk is usually partial.
        throughput_bps = (self.uploaded_bytes / elapsed) if elapsed > 0 else 0.0

        return {
            'uploaded_bytes': self.uploaded_bytes,
            'elapsed_seconds': elapsed,
            'throughput_bytes_per_sec': throughput_bps,
            'throughput_mb_per_sec': throughput_bps / (1024 ** 2),
            'parts_uploaded': len(self.parts),
        }

    # -- internal -----------------------------------------------------------

    def _upload_part_with_retry(self, part_number: int, data: bytes) -> str:
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.s3_client.upload_part(
                    Bucket=self.bucket,
                    Key=self.key,
                    UploadId=self.upload_id,
                    PartNumber=part_number,
                    Body=data,
                )
                return response['ETag'].strip('"')
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "upload_part failed for part %d (attempt %d/%d): %s",
                    part_number, attempt, MAX_RETRIES, exc,
                )
                if attempt < MAX_RETRIES:
                    backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    time.sleep(backoff)

        # All retries exhausted.
        raise last_exc

    def _abort(self, reason: str) -> None:
        if self.upload_id is None:
            return
        try:
            self.s3_client.abort_multipart_upload(
                Bucket=self.bucket, Key=self.key, UploadId=self.upload_id
            )
            logger.error("Aborted multipart upload %s: %s", self.upload_id, reason)
        except Exception as exc:
            logger.error(
                "Failed to abort multipart upload %s during cleanup: %s",
                self.upload_id, exc,
            )
        finally:
            self.upload_id = None
            self.parts = []
