"""
TRIAGE CLASSIFIER FOR FORENSIC EVIDENCE PIPELINE

Responsible ONLY for:
    * Reading a memory dump page-by-page.
    * Zero-page detection.
    * Entropy calculation.
    * Weighted forensic page scoring (entropy + keyword + executable header).
    * Page classification (ZERO_PAGE / LOW_VALUE / MEDIUM_VALUE / HIGH_VALUE).
    * Page SHA-256 generation.
    * Yielding retained pages + per-page metadata.
"""

import hashlib
from typing import Dict, Iterator, List, Tuple

import numpy as np

# ============================================================================
# CONFIGURATION
# ============================================================================
#
# All weights and thresholds used by the scoring model live here. Nothing
# below this section should contain a hardcoded weight or threshold value.

PAGE_SIZE = 4096

# --- Entropy scoring ---------------------------------------------------
# Shannon entropy is normalized from its theoretical range (0-8 bits/byte)
# onto a 0-ENTROPY_SCORE_MAX point scale. Higher entropy => higher score.
ENTROPY_BITS_MAX = 8.0
ENTROPY_SCORE_MAX = 40.0

# --- Keyword scoring ---------------------------------------------------
# The raw page is decoded as UTF-8 (errors ignored) and searched, case
# insensitively, for each keyword below. Each occurrence contributes its
# configured weight to the keyword score, which is then capped at
# KEYWORD_SCORE_MAX so no single page can be dominated by one repeated term.
KEYWORD_SCORE_MAX = 40.0
KEYWORD_WEIGHTS: Dict[str, float] = {
    "password": 8,
    "passwd": 8,
    "secret": 8,
    "token": 6,
    "apikey": 8,
    "api_key": 8,
    "aws": 5,
    "aws_secret": 10,
    "access_key": 8,
    "private key": 10,
    "ssh-rsa": 10,
    "begin rsa": 10,
    "begin private key": 10,
    "authorization": 6,
    "bearer": 6,
    "cookie": 5,
    "session": 4,
}

# --- Executable header detection ----------------------------------------
EXECUTABLE_HEADER_SCORE = 20.0
EXECUTABLE_SIGNATURES: Tuple[bytes, ...] = (
    b"\x7fELF",  # ELF (Linux/Unix executables and shared objects)
    b"MZ",       # DOS/PE header (Windows executables and DLLs)
)

# --- Classification thresholds ------------------------------------------
# score <  LOW_VALUE_MAX_SCORE                       -> LOW_VALUE
# LOW_VALUE_MAX_SCORE <= score < MEDIUM_VALUE_MAX_SCORE -> MEDIUM_VALUE
# score >= MEDIUM_VALUE_MAX_SCORE                     -> HIGH_VALUE
LOW_VALUE_MAX_SCORE = 20.0
MEDIUM_VALUE_MAX_SCORE = 50.0

# --- Drop policy ---------------------------------------------------------
# A LOW_VALUE page is only dropped if it has no keyword matches, no
# executable header, AND its entropy falls below this threshold. This is
# the same knob previously exposed as the extractor's "threshold" argument.
DEFAULT_LOW_VALUE_ENTROPY_THRESHOLD = 1.0

# --- Category labels -------------------------------------------------------
CATEGORY_ZERO_PAGE = "ZERO_PAGE"
CATEGORY_LOW_VALUE = "LOW_VALUE"
CATEGORY_MEDIUM_VALUE = "MEDIUM_VALUE"
CATEGORY_HIGH_VALUE = "HIGH_VALUE"


# ============================================================================
# CORE TRIAGE PRIMITIVES
# ============================================================================

def calculate_entropy(page: bytes) -> float:
    """Calculate Shannon entropy of a page, in bits/byte (0.0 - 8.0)."""
    arr = np.frombuffer(page, dtype=np.uint8)
    counts = np.bincount(arr, minlength=256)
    probs = counts[counts > 0] / arr.size
    return float(-np.sum(probs * np.log2(probs)))


def is_zero_page(page: bytes) -> bool:
    """Return True if the page consists entirely of zero bytes."""
    arr = np.frombuffer(page, dtype=np.uint8)
    return not arr.any()


def normalize_entropy_score(entropy: float) -> float:
    """Scale raw Shannon entropy onto a 0-ENTROPY_SCORE_MAX point scale."""
    normalized = (entropy / ENTROPY_BITS_MAX) * ENTROPY_SCORE_MAX
    return float(min(max(normalized, 0.0), ENTROPY_SCORE_MAX))


def compute_keyword_score(page: bytes) -> Tuple[float, List[str]]:
    """
    Search page bytes for forensic keywords (UTF-8, errors ignored,
    case-insensitive). Returns (capped_score, list_of_matched_keywords).
    """
    text = page.decode("utf-8", errors="ignore").lower()

    matched_keywords: List[str] = []
    raw_score = 0.0

    for keyword, weight in KEYWORD_WEIGHTS.items():
        occurrences = text.count(keyword)
        if occurrences > 0:
            matched_keywords.append(keyword)
            raw_score += weight * occurrences

    capped_score = min(raw_score, KEYWORD_SCORE_MAX)
    return capped_score, matched_keywords


def detect_executable_header(page: bytes) -> bool:
    """Return True if the page contains a known executable signature."""
    return any(signature in page for signature in EXECUTABLE_SIGNATURES)


def categorize_score(score: float) -> str:
    """Map a total forensic score onto a classification category."""
    if score < LOW_VALUE_MAX_SCORE:
        return CATEGORY_LOW_VALUE
    if score < MEDIUM_VALUE_MAX_SCORE:
        return CATEGORY_MEDIUM_VALUE
    return CATEGORY_HIGH_VALUE


def decide_keep(
    category: str,
    keyword_matches: List[str],
    executable_header: bool,
    entropy: float,
    low_value_entropy_threshold: float,
) -> bool:
    """
    Decide whether a page should be retained.

    * ZERO_PAGE is handled separately (always dropped) before this is called.
    * MEDIUM_VALUE and HIGH_VALUE pages are always retained.
    * LOW_VALUE pages are dropped only if they have no keyword matches, no
      executable header, and entropy below low_value_entropy_threshold.
    """
    if category == CATEGORY_LOW_VALUE:
        no_signal = not keyword_matches and not executable_header
        if no_signal and entropy < low_value_entropy_threshold:
            return False
        return True

    # MEDIUM_VALUE / HIGH_VALUE
    return True


def score_page(page: bytes, low_value_entropy_threshold: float = DEFAULT_LOW_VALUE_ENTROPY_THRESHOLD) -> Dict:
    """
    Run the full forensic scoring pipeline on a single page.

    Returns a dict with keys: keep, category, score, entropy,
    keyword_matches, executable_header.
    """
    if is_zero_page(page):
        return {
            "keep": False,
            "category": CATEGORY_ZERO_PAGE,
            "score": 0.0,
            "entropy": 0.0,
            "keyword_matches": [],
            "executable_header": False,
        }

    entropy = calculate_entropy(page)
    entropy_score = normalize_entropy_score(entropy)
    keyword_score, keyword_matches = compute_keyword_score(page)
    executable_header = detect_executable_header(page)
    exec_score = EXECUTABLE_HEADER_SCORE if executable_header else 0.0

    total_score = entropy_score + keyword_score + exec_score
    category = categorize_score(total_score)
    keep = decide_keep(category, keyword_matches, executable_header, entropy, low_value_entropy_threshold)

    return {
        "keep": keep,
        "category": category,
        "score": total_score,
        "entropy": entropy,
        "keyword_matches": keyword_matches,
        "executable_header": executable_header,
    }


def sha256_page(page: bytes) -> bytes:
    """Compute SHA-256 hash of a page."""
    return hashlib.sha256(page).digest()


# ============================================================================
# TRIAGE STREAMING EXTRACTOR
# ============================================================================

class TriageStreamingExtractor:
    """
    Reads a dump file, applies weighted forensic triage classification
    page-by-page, and yields retained pages along with their metadata.

    This is the single, clean interface the chunk streamer (or any other
    downstream consumer) uses to receive evidence:

        extractor = TriageStreamingExtractor(dump_file)
        for page_bytes, metadata in extractor.extract():
            chunk_streamer.add_page(page_bytes, metadata)

    The constructor's ``threshold`` argument is the entropy threshold used
    when deciding whether to drop a LOW_VALUE page that has no keyword
    matches and no executable header (see decide_keep()).
    """

    def __init__(self, filename: str, threshold: float = DEFAULT_LOW_VALUE_ENTROPY_THRESHOLD):
        self.filename = filename
        self.threshold = threshold
        self.page_number = 0
        self.kept = 0
        self.dropped = 0

        # Category / score bookkeeping for stats()
        self.zero_pages = 0
        self.low_value_pages = 0
        self.medium_value_pages = 0
        self.high_value_pages = 0
        self._score_total = 0.0
        self._entropy_total = 0.0

    def extract(self) -> Iterator[Tuple[bytes, Dict]]:
        """
        Generator that yields (page_bytes, metadata) for retained pages.

        metadata contains: page_number, entropy, sha256, page_bytes (size),
        category, score, keyword_matches, executable_header.
        """
        with open(self.filename, "rb") as f:
            while True:
                page = f.read(PAGE_SIZE)
                if not page:
                    break

                result = score_page(page, self.threshold)

                self._tally_category(result["category"])
                self._score_total += result["score"]
                self._entropy_total += result["entropy"]

                if result["keep"]:
                    page_hash = sha256_page(page)
                    metadata = {
                        'page_number': self.page_number,
                        'entropy': round(result["entropy"], 4),
                        'sha256': page_hash.hex(),
                        'page_bytes': len(page),
                        'category': result["category"],
                        'score': round(result["score"], 4),
                        'keyword_matches': result["keyword_matches"],
                        'executable_header': result["executable_header"],
                    }
                    self.kept += 1
                    yield page, metadata
                else:
                    self.dropped += 1

                self.page_number += 1

    def _tally_category(self, category: str) -> None:
        """Update per-category page counters."""
        if category == CATEGORY_ZERO_PAGE:
            self.zero_pages += 1
        elif category == CATEGORY_LOW_VALUE:
            self.low_value_pages += 1
        elif category == CATEGORY_MEDIUM_VALUE:
            self.medium_value_pages += 1
        elif category == CATEGORY_HIGH_VALUE:
            self.high_value_pages += 1

    def stats(self) -> Dict:
        """Return processing statistics."""
        return {
            'total_pages': self.page_number,
            'kept_pages': self.kept,
            'dropped_pages': self.dropped,
            'drop_rate_pct': (self.dropped / self.page_number * 100) if self.page_number > 0 else 0,
            'zero_pages': self.zero_pages,
            'low_value_pages': self.low_value_pages,
            'medium_value_pages': self.medium_value_pages,
            'high_value_pages': self.high_value_pages,
            'average_score': (self._score_total / self.page_number) if self.page_number > 0 else 0.0,
            'average_entropy': (self._entropy_total / self.page_number) if self.page_number > 0 else 0.0,
        }
