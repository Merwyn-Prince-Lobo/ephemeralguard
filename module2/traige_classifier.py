"""
TRIAGE CLASSIFIER FOR FORENSIC EVIDENCE PIPELINE

Responsible ONLY for:
    * Reading a memory dump page-by-page.
    * Zero-page detection.
    * Entropy calculation.
    * Page classification (keep / drop).
    * Page SHA-256 generation.
    * Yielding retained pages + per-page metadata.

"""

import hashlib
from typing import Dict, Iterator, Tuple

import numpy as np

# ============================================================================
# CONSTANTS
# ============================================================================

PAGE_SIZE = 4096
ENTROPY_THRESHOLD = 1.0


# ============================================================================
# CORE TRIAGE PRIMITIVES
# ============================================================================

def calculate_entropy(page: bytes) -> float:
    """Calculate Shannon entropy of a page."""
    arr = np.frombuffer(page, dtype=np.uint8)
    counts = np.bincount(arr, minlength=256)
    probs = counts[counts > 0] / arr.size
    return float(-np.sum(probs * np.log2(probs)))


def classify_page(page: bytes, threshold: float = ENTROPY_THRESHOLD) -> Tuple[bool, float]:
    """Classify a page: keep or skip. Returns (keep, entropy)."""
    arr = np.frombuffer(page, dtype=np.uint8)

    # Zero-page detection
    if not arr.any():
        return False, 0.0

    entropy = calculate_entropy(page)

    if entropy < threshold:
        return False, entropy

    return True, entropy


def sha256_page(page: bytes) -> bytes:
    """Compute SHA-256 hash of a page."""
    return hashlib.sha256(page).digest()


# ============================================================================
# TRIAGE STREAMING EXTRACTOR
# ============================================================================

class TriageStreamingExtractor:
    """
    Reads a dump file, applies triage classification page-by-page, and
    yields retained pages along with their forensic metadata.

    This is the single, clean interface the chunk streamer (or any other
    downstream consumer) uses to receive evidence:

        extractor = TriageStreamingExtractor(dump_file)
        for page_bytes, metadata in extractor.extract():
            chunk_streamer.add_page(page_bytes, metadata)
    """

    def __init__(self, filename: str, threshold: float = ENTROPY_THRESHOLD):
        self.filename = filename
        self.threshold = threshold
        self.page_number = 0
        self.kept = 0
        self.dropped = 0

    def extract(self) -> Iterator[Tuple[bytes, Dict]]:
        """
        Generator that yields (page_bytes, metadata) for retained pages.
        metadata contains: page_number, entropy, sha256, page_bytes (size).
        """
        with open(self.filename, "rb") as f:
            while True:
                page = f.read(PAGE_SIZE)
                if not page:
                    break

                keep, entropy = classify_page(page, self.threshold)

                if keep:
                    page_hash = sha256_page(page)
                    metadata = {
                        'page_number': self.page_number,
                        'entropy': round(entropy, 4),
                        'sha256': page_hash.hex(),
                        'page_bytes': len(page),
                    }
                    self.kept += 1
                    yield page, metadata
                else:
                    self.dropped += 1

                self.page_number += 1

    def stats(self) -> Dict:
        """Return processing statistics."""
        return {
            'total_pages': self.page_number,
            'kept_pages': self.kept,
            'dropped_pages': self.dropped,
            'drop_rate_pct': (self.dropped / self.page_number * 100) if self.page_number > 0 else 0,
        }
