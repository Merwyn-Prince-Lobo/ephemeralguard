"""
CHUNK STREAMER FOR FORENSIC EVIDENCE PIPELINE

Responsible for:
    * Accepting retained pages from the triage classifier (via add_page()
      or the stream() generator helper).
    * Buffering pages into configurable chunks (default 64 MB).
    * Tracking chunk id, chunk offset, and bytes produced per chunk.
    * Maintaining the running SHA-256 hash of the complete evidence stream.
    * Building the forensic Merkle tree from PAGE hashes (not chunk hashes).
    * Computing chunk-level SHA-256 hashes for transport/upload verification.
    * Producing completed chunks (and flushing the final partial chunk).
    * Populating the page-level manifest.

This module is intentionally independent of S3/AWS and all network code.
Uploading completed Chunk objects is the job of secure_transfer.py.
"""

import hashlib
import time
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB chunks


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class ManifestEntry:
    """Single manifest entry for one retained page."""
    page_number: int
    entropy: float
    sha256: str          # page hash from triage
    chunk_id: int         # which chunk contains this page
    chunk_offset: int     # byte offset of this page within its chunk


@dataclass
class Chunk:
    """A completed, ready-to-upload chunk."""
    chunk_id: int
    offset: int            # byte offset of this chunk within the full stream
    data: bytes
    sha256: str            # chunk hash, for transport/upload verification only
    num_pages: int


# ============================================================================
# MERKLE TREE (built from forensic PAGE hashes)
# ============================================================================

def build_merkle_root(hashes: List[bytes]) -> Optional[str]:
    """Build a Merkle tree over the given list of hashes and return the root."""
    if not hashes:
        return None

    current = hashes[:]

    while len(current) > 1:
        if len(current) % 2:
            current.append(current[-1])

        next_level = []
        for i in range(0, len(current), 2):
            combined = current[i] + current[i + 1]
            next_level.append(hashlib.sha256(combined).digest())

        current = next_level

    return current[0].hex()


# ============================================================================
# CHUNK STREAMER
# ============================================================================

class ChunkStreamer:
    """
    Buffers retained forensic pages into configurable chunks and tracks all
    integrity metadata needed for later upload and verification:

        * self.manifest      - per-page ManifestEntry list
        * self.chunk_hashes  - per-chunk SHA-256 (transport verification only)
        * page-level Merkle root (the forensic evidence's own integrity proof)
        * running SHA-256 over the complete evidence stream

    Usage:

        streamer = ChunkStreamer(case_id="CASE-2024-001")
        for page_bytes, metadata in extractor.extract():
            chunk = streamer.add_page(page_bytes, metadata)
            if chunk is not None:
                secure_transfer.upload_chunk(chunk)
        final = streamer.flush()
        if final is not None:
            secure_transfer.upload_chunk(final)

    or, equivalently, using the stream() convenience generator:

        for chunk in streamer.stream(extractor.extract()):
            secure_transfer.upload_chunk(chunk)
    """

    def __init__(self, case_id: str, chunk_size: int = CHUNK_SIZE):
        self.case_id = case_id
        self.chunk_size = chunk_size

        self._buffer = bytearray()
        self._buffer_pages = 0

        self.chunk_id = 0
        self.total_offset = 0

        self.manifest: List[ManifestEntry] = []
        self.chunk_hashes: List[str] = []
        self.page_hashes: List[bytes] = []

        self.full_hash = hashlib.sha256()
        self.retained_pages = 0

    # -- public interface used by the triage classifier -------------------

    def add_page(self, page_bytes: bytes, metadata: Dict) -> Optional[Chunk]:
        """
        Accept one retained page and its triage metadata.

        Returns a completed Chunk if this page filled the current buffer,
        otherwise returns None (page was buffered, nothing to upload yet).
        """
        chunk_offset_for_page = len(self._buffer)

        self._buffer.extend(page_bytes)
        self._buffer_pages += 1
        self.retained_pages += 1

        # Running hash of the full evidence stream.
        self.full_hash.update(page_bytes)

        # Page hash feeds the forensic Merkle tree.
        page_hash_bytes = bytes.fromhex(metadata['sha256'])
        self.page_hashes.append(page_hash_bytes)

        # Manifest entry - the only place chunk_id/chunk_offset are known.
        self.manifest.append(ManifestEntry(
            page_number=metadata['page_number'],
            entropy=metadata['entropy'],
            sha256=metadata['sha256'],
            chunk_id=self.chunk_id,
            chunk_offset=chunk_offset_for_page,
        ))

        if len(self._buffer) >= self.chunk_size:
            return self._finalize_chunk()
        return None

    def stream(self, retained_pages: Iterator[Tuple[bytes, Dict]]) -> Iterator[Chunk]:
        """
        Convenience generator: consumes (page, metadata) tuples - e.g. from
        TriageStreamingExtractor.extract() - and yields completed chunks,
        including the final flushed partial chunk at the end.
        """
        for page, metadata in retained_pages:
            chunk = self.add_page(page, metadata)
            if chunk is not None:
                yield chunk

        final = self.flush()
        if final is not None:
            yield final

    def flush(self) -> Optional[Chunk]:
        """Flush the final partial chunk, if any data remains buffered."""
        if len(self._buffer) == 0:
            return None
        return self._finalize_chunk()

    # -- integrity / metadata accessors ------------------------------------

    def get_merkle_root(self) -> Optional[str]:
        """Merkle tree built from retained PAGE hashes (the forensic evidence)."""
        return build_merkle_root(self.page_hashes)

    def get_full_hash(self) -> str:
        """Running SHA-256 over the complete evidence stream."""
        return self.full_hash.hexdigest()

    def get_manifest(self) -> List[ManifestEntry]:
        return self.manifest

    def get_metadata(self, page_size: int) -> Dict:
        """
        Forensic metadata to be stored alongside the uploaded evidence:
        overall SHA-256, Merkle root, page size, chunk size, retained page
        count, creation timestamp, and case id.
        """
        return {
            'case_id': self.case_id,
            'overall_sha256': self.get_full_hash(),
            'merkle_root': self.get_merkle_root(),
            'page_size': page_size,
            'chunk_size': self.chunk_size,
            'retained_pages': self.retained_pages,
            'created_at': time.time(),
        }

    # -- internal -----------------------------------------------------------

    def _finalize_chunk(self) -> Chunk:
        data = bytes(self._buffer)
        chunk_hash = hashlib.sha256(data).hexdigest()
        self.chunk_hashes.append(chunk_hash)

        chunk = Chunk(
            chunk_id=self.chunk_id,
            offset=self.total_offset,
            data=data,
            sha256=chunk_hash,
            num_pages=self._buffer_pages,
        )

        self.total_offset += len(data)
        self.chunk_id += 1
        self._buffer = bytearray()
        self._buffer_pages = 0

        return chunk
