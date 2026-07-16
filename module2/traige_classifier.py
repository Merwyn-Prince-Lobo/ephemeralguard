"""
TRIAGE CLASSIFIER FOR FORENSIC EVIDENCE PIPELINE

Responsible ONLY for:
    * Reading a memory dump page-by-page.
    * Zero-page detection.
    * Context-aware weighted forensic page scoring (entropy + modular
      keyword categories + executable signature + optional attack context).
    * Page classification (ZERO_PAGE / LOW_VALUE / MEDIUM_VALUE / HIGH_VALUE).
    * Evidence confidence derivation.
    * Page SHA-256 generation.
    * Yielding retained pages + per-page metadata.

This module implements a context-aware selective memory acquisition
framework: page-level forensic scoring that combines entropy analysis,
weighted forensic keyword indicators, executable signature detection, and
an optional attack context to prioritize high-value forensic evidence,
while preserving the existing streaming acquisition pipeline, public
interfaces, and downstream module contracts unchanged.

Architecture (unchanged):

    Memory Dump -> TriageStreamingExtractor -> ChunkStreamer
                -> SecureTransferModule -> S3

``TriageStreamingExtractor`` remains a drop-in replacement: its
constructor signature is backwards compatible (the new ``context``
argument is optional and defaults to ``None``), ``extract()`` still yields
``(page_bytes, metadata)`` tuples with all previously existing metadata
keys intact, and ``stats()`` still returns all previously existing keys
intact. Downstream modules (ChunkStreamer, SecureTransferModule) require
zero code changes.
"""

import hashlib
import math
import time
from typing import Callable, Dict, Iterator, List, Optional, Tuple

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

# --- Keyword scoring -----------------------------------------------------
# The raw page is decoded as UTF-8 (errors ignored) and searched, case
# insensitively, for each keyword below. Each occurrence contributes its
# configured weight to the keyword score, which is then capped at
# KEYWORD_SCORE_MAX so no single page can be dominated by one repeated term.
#
# Keywords are grouped into small, independent categories (credentials,
# cloud, ssh, database) instead of one flat dictionary, so each forensic
# indicator family can be scored, tested, and extended in isolation.
KEYWORD_SCORE_MAX = 40.0

CREDENTIAL_KEYWORDS: Dict[str, float] = {
    "password": 10,
    "passwd": 8,
    "secret": 8,
    "token": 8,
    "apikey": 8,
    "api_key": 8,
    "authorization": 6,
    "bearer": 6,
    "cookie": 4,
    "session": 4,
}

CLOUD_KEYWORDS: Dict[str, float] = {
    "aws_secret_access_key": 20,
    "aws_secret": 12,
    "access_key": 8,
    "aws": 5,
}

SSH_KEYWORDS: Dict[str, float] = {
    "begin private key": 20,
    "begin rsa": 15,
    "ssh-rsa": 15,
    "id_rsa": 12,
}

DATABASE_KEYWORDS: Dict[str, float] = {
    "mysql": 5,
    "postgres": 5,
    "mongodb": 5,
    "redis": 5,
}

# --- Executable header detection ------------------------------------------
EXECUTABLE_HEADER_SCORE = 20.0
EXECUTABLE_SIGNATURES: Tuple[bytes, ...] = (
    b"\x7fELF",  # ELF (Linux/Unix executables and shared objects)
    b"MZ",       # DOS/PE header (Windows executables and DLLs)
)

# --- Context-aware scoring -------------------------------------------------
# When an attack/trigger context is supplied to the extractor, additional
# forensic indicators relevant to that context contribute extra score, on
# top of (not instead of) the baseline entropy/keyword/signature score.
# With no context (the default), this stage contributes 0 and the
# algorithm behaves exactly as it did before context-awareness existed.
CONTEXT_SCORE_MAX = 20.0

CONTEXT_CREDENTIAL_THEFT = "credential_theft"
CONTEXT_CRYPTOMINING = "cryptomining"
CONTEXT_NETWORK_EXFILTRATION = "network_exfiltration"

# Extra weight given to indicators that are especially significant for the
# "credential_theft" context, on top of their baseline keyword score.
CREDENTIAL_THEFT_CONTEXT_KEYWORDS: Dict[str, float] = {
    "password": 4,
    "secret": 4,
    "aws_secret_access_key": 6,
    "access_key": 4,
    "ssh-rsa": 4,
    "begin private key": 6,
    "begin rsa": 4,
    "id_rsa": 4,
}

# Indicators specific to coin-mining tooling, plus a bonus if the page also
# carries an executable signature (miners are typically shipped as ELF/PE
# binaries or dropped in-memory).
CRYPTOMINING_CONTEXT_KEYWORDS: Dict[str, float] = {
    "xmrig": 15,
    "minerd": 15,
    "cpuminer": 15,
    "stratum+tcp": 10,
}
CRYPTOMINING_EXECUTABLE_BONUS = 10.0

# Indicators relevant to outbound network exfiltration.
NETWORK_EXFILTRATION_CONTEXT_KEYWORDS: Dict[str, float] = {
    "curl": 6,
    "wget": 6,
    "socket": 5,
    "http": 4,
    "tls": 4,
}

# --- Confidence scoring ----------------------------------------------------
# Evidence confidence is derived from the total forensic score using a
# logistic curve: scores near/above the HIGH_VALUE threshold map to high
# confidence, low scores map to low confidence, with a smooth transition
# around the midpoint rather than a hard cutoff.
CONFIDENCE_MIDPOINT = 50.0
CONFIDENCE_STEEPNESS = 0.06

# --- Classification thresholds ---------------------------------------------
# score <  LOW_VALUE_MAX_SCORE                       -> LOW_VALUE
# LOW_VALUE_MAX_SCORE <= score < MEDIUM_VALUE_MAX_SCORE -> MEDIUM_VALUE
# score >= MEDIUM_VALUE_MAX_SCORE                     -> HIGH_VALUE
LOW_VALUE_MAX_SCORE = 20.0
MEDIUM_VALUE_MAX_SCORE = 50.0

# --- Drop policy -------------------------------------------------------
# A LOW_VALUE page is only dropped if it has no keyword matches, no
# executable header, AND its entropy falls below this threshold. This is
# the same knob previously exposed as the extractor's "threshold" argument.
DEFAULT_LOW_VALUE_ENTROPY_THRESHOLD = 1.0

# --- Category labels ---------------------------------------------------
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


def entropy_score(entropy: float) -> float:
    """Scale raw Shannon entropy onto a 0-ENTROPY_SCORE_MAX point scale."""
    normalized = (entropy / ENTROPY_BITS_MAX) * ENTROPY_SCORE_MAX
    return float(min(max(normalized, 0.0), ENTROPY_SCORE_MAX))


# Backwards-compatible alias for the previous function name.
normalize_entropy_score = entropy_score


def decode_page_text(page: bytes) -> str:
    """Decode a page as UTF-8 (errors ignored), lower-cased, once per page."""
    return page.decode("utf-8", errors="ignore").lower()


def _match_keyword_category(text: str, keyword_weights: Dict[str, float]) -> Tuple[float, List[str]]:
    """
    Search ``text`` for every keyword in ``keyword_weights`` (case handled
    by the caller). Returns (raw_score, matched_keywords). Shared by every
    keyword category helper and every context helper, so matching logic
    lives in exactly one place.
    """
    matched: List[str] = []
    raw_score = 0.0
    for keyword, weight in keyword_weights.items():
        occurrences = text.count(keyword)
        if occurrences > 0:
            matched.append(keyword)
            raw_score += weight * occurrences
    return raw_score, matched


def credential_keyword_score(text: str) -> Tuple[float, List[str]]:
    """Score credential-related indicators (password, token, cookie, ...)."""
    return _match_keyword_category(text, CREDENTIAL_KEYWORDS)


def cloud_keyword_score(text: str) -> Tuple[float, List[str]]:
    """Score cloud-credential indicators (AWS keys, access keys, ...)."""
    return _match_keyword_category(text, CLOUD_KEYWORDS)


def ssh_keyword_score(text: str) -> Tuple[float, List[str]]:
    """Score SSH/private-key indicators."""
    return _match_keyword_category(text, SSH_KEYWORDS)


def database_keyword_score(text: str) -> Tuple[float, List[str]]:
    """Score database-connection indicators."""
    return _match_keyword_category(text, DATABASE_KEYWORDS)


# Registry of keyword category scorers. Adding a new forensic keyword
# family only requires adding a dict + a function + one entry here.
KEYWORD_CATEGORY_SCORERS: Dict[str, Callable[[str], Tuple[float, List[str]]]] = {
    "credentials": credential_keyword_score,
    "cloud": cloud_keyword_score,
    "ssh": ssh_keyword_score,
    "database": database_keyword_score,
}


def keyword_score(page_text: str) -> Tuple[float, List[str], Dict[str, float]]:
    """
    Aggregate every keyword category scorer over an already-decoded page.

    Returns (capped_score, all_matched_keywords, per_category_raw_scores).
    The total is capped at KEYWORD_SCORE_MAX so no single page can be
    dominated by one repeated term or category.
    """
    all_matches: List[str] = []
    category_scores: Dict[str, float] = {}
    raw_total = 0.0

    for category_name, scorer in KEYWORD_CATEGORY_SCORERS.items():
        raw, matches = scorer(page_text)
        category_scores[category_name] = raw
        raw_total += raw
        all_matches.extend(matches)

    capped_score = min(raw_total, KEYWORD_SCORE_MAX)
    return capped_score, all_matches, category_scores


def compute_keyword_score(page: bytes) -> Tuple[float, List[str]]:
    """
    Backwards-compatible wrapper around keyword_score() that accepts raw
    page bytes (decodes internally) and returns the (score, matches) pair
    the original implementation returned.
    """
    text = decode_page_text(page)
    score, matches, _ = keyword_score(text)
    return score, matches


def detect_executable_header(page: bytes) -> bool:
    """Return True if the page contains a known executable signature."""
    return any(signature in page for signature in EXECUTABLE_SIGNATURES)


def signature_score(executable_header: bool) -> float:
    """Score contribution from executable-signature detection."""
    return EXECUTABLE_HEADER_SCORE if executable_header else 0.0


# --- Context-aware scoring helpers -----------------------------------------

def _credential_theft_context(text: str, executable_header: bool) -> Tuple[float, List[str]]:
    return _match_keyword_category(text, CREDENTIAL_THEFT_CONTEXT_KEYWORDS)


def _cryptomining_context(text: str, executable_header: bool) -> Tuple[float, List[str]]:
    raw, matches = _match_keyword_category(text, CRYPTOMINING_CONTEXT_KEYWORDS)
    if executable_header:
        raw += CRYPTOMINING_EXECUTABLE_BONUS
    return raw, matches


def _network_exfiltration_context(text: str, executable_header: bool) -> Tuple[float, List[str]]:
    return _match_keyword_category(text, NETWORK_EXFILTRATION_CONTEXT_KEYWORDS)


# Registry of context scorers. Adding a new attack context (e.g. future
# "lateral_movement" or "ransomware" contexts) only requires a keyword
# dict + a function + one entry here - no changes to score_page() or the
# extractor.
CONTEXT_SCORERS: Dict[str, Callable[[str, bool], Tuple[float, List[str]]]] = {
    CONTEXT_CREDENTIAL_THEFT: _credential_theft_context,
    CONTEXT_CRYPTOMINING: _cryptomining_context,
    CONTEXT_NETWORK_EXFILTRATION: _network_exfiltration_context,
}


def context_score(
    page_text: str,
    executable_header: bool,
    context: Optional[str],
) -> Tuple[float, List[str]]:
    """
    Compute the additional score contributed by an attack/trigger context.

    If ``context`` is None (the default) or not a recognized context, this
    returns (0.0, []) and has no effect on the total score - i.e. the
    algorithm behaves exactly as it did before context-awareness existed.
    """
    if not context:
        return 0.0, []

    scorer = CONTEXT_SCORERS.get(context)
    if scorer is None:
        return 0.0, []

    raw, matches = scorer(page_text, executable_header)
    capped = min(raw, CONTEXT_SCORE_MAX)
    return capped, matches


def calculate_total_score(
    entropy_pts: float,
    keyword_pts: float,
    signature_pts: float,
    context_pts: float = 0.0,
) -> float:
    """Combine every scoring component into the final forensic score."""
    return entropy_pts + keyword_pts + signature_pts + context_pts


def classify_page(score: float) -> str:
    """Map a total forensic score onto a classification category."""
    if score < LOW_VALUE_MAX_SCORE:
        return CATEGORY_LOW_VALUE
    if score < MEDIUM_VALUE_MAX_SCORE:
        return CATEGORY_MEDIUM_VALUE
    return CATEGORY_HIGH_VALUE


# Backwards-compatible alias for the previous function name.
categorize_score = classify_page


def confidence_score(score: float) -> float:
    """
    Derive an evidence confidence percentage (0-100) from the total
    forensic score using a logistic curve centered at CONFIDENCE_MIDPOINT.
    Higher scores approach 100% confidence; low scores approach 0%.
    """
    exponent = -(score - CONFIDENCE_MIDPOINT) * CONFIDENCE_STEEPNESS
    confidence = 100.0 / (1.0 + math.exp(exponent))
    return float(min(max(confidence, 0.0), 100.0))


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


def score_page(
    page: bytes,
    low_value_entropy_threshold: float = DEFAULT_LOW_VALUE_ENTROPY_THRESHOLD,
    context: Optional[str] = None,
) -> Dict:
    """
    Run the full context-aware forensic scoring pipeline on a single page.

    Returns a dict with keys: keep, category, score, confidence, entropy,
    entropy_score, keyword_matches, keyword_score, executable_header,
    signature_score, context_used, context_matches.
    """
    if is_zero_page(page):
        return {
            "keep": False,
            "category": CATEGORY_ZERO_PAGE,
            "score": 0.0,
            "confidence": 0.0,
            "entropy": 0.0,
            "entropy_score": 0.0,
            "keyword_matches": [],
            "keyword_score": 0.0,
            "executable_header": False,
            "signature_score": 0.0,
            "context_used": context,
            "context_matches": [],
        }

    entropy = calculate_entropy(page)
    entropy_pts = entropy_score(entropy)

    page_text = decode_page_text(page)
    keyword_pts, keyword_matches, _category_scores = keyword_score(page_text)

    executable_header = detect_executable_header(page)
    signature_pts = signature_score(executable_header)

    context_pts, context_matches = context_score(page_text, executable_header, context)

    total_score = calculate_total_score(entropy_pts, keyword_pts, signature_pts, context_pts)
    category = classify_page(total_score)
    confidence = confidence_score(total_score)
    keep = decide_keep(category, keyword_matches, executable_header, entropy, low_value_entropy_threshold)

    return {
        "keep": keep,
        "category": category,
        "score": total_score,
        "confidence": confidence,
        "entropy": entropy,
        "entropy_score": entropy_pts,
        "keyword_matches": keyword_matches,
        "keyword_score": keyword_pts,
        "executable_header": executable_header,
        "signature_score": signature_pts,
        "context_used": context,
        "context_matches": context_matches,
    }


def sha256_page(page: bytes) -> bytes:
    """Compute SHA-256 hash of a page."""
    return hashlib.sha256(page).digest()


# ============================================================================
# TRIAGE STREAMING EXTRACTOR
# ============================================================================

class TriageStreamingExtractor:
    """
    Reads a dump file, applies context-aware weighted forensic triage
    classification page-by-page, and yields retained pages along with
    their metadata.

    This is the single, clean interface the chunk streamer (or any other
    downstream consumer) uses to receive evidence:

        extractor = TriageStreamingExtractor(dump_file)
        for page_bytes, metadata in extractor.extract():
            chunk_streamer.add_page(page_bytes, metadata)

    The constructor's ``threshold`` argument is the entropy threshold used
    when deciding whether to drop a LOW_VALUE page that has no keyword
    matches and no executable header (see decide_keep()).

    The constructor's ``context`` argument is optional and defaults to
    ``None``. When omitted, scoring behaves exactly as before context
    awareness was added. When supplied (e.g. "credential_theft",
    "cryptomining", "network_exfiltration"), page scoring additionally
    weights indicators relevant to that attack context. This argument is
    purely internal to the extractor - it does not change the public
    interface downstream modules rely on (extract()/stats() signatures
    and existing return keys are unchanged).
    """

    def __init__(
        self,
        filename: str,
        threshold: float = DEFAULT_LOW_VALUE_ENTROPY_THRESHOLD,
        context: Optional[str] = None,
    ):
        self.filename = filename
        self.threshold = threshold
        self.context = context
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
        self._start_time: Optional[float] = None
        self._elapsed_seconds = 0.0

    def extract(self) -> Iterator[Tuple[bytes, Dict]]:
        """
        Generator that yields (page_bytes, metadata) for retained pages.

        metadata contains: page_number, entropy, sha256, page_bytes (size),
        category, score, keyword_matches, executable_header (all present
        before context-aware scoring was added), plus confidence,
        keyword_score, entropy_score, signature_score, context_used.
        """
        self._start_time = time.perf_counter()
        with open(self.filename, "rb") as f:
            while True:
                page = f.read(PAGE_SIZE)
                if not page:
                    break

                result = score_page(page, self.threshold, self.context)

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
                        # -- new context-aware fields --
                        'confidence': round(result["confidence"], 2),
                        'keyword_score': round(result["keyword_score"], 4),
                        'entropy_score': round(result["entropy_score"], 4),
                        'signature_score': round(result["signature_score"], 4),
                        'context_used': result["context_used"],
                    }
                    self.kept += 1
                    yield page, metadata
                else:
                    self.dropped += 1

                self.page_number += 1
        self._elapsed_seconds = time.perf_counter() - self._start_time

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
            # -- new fields --
            'processing_time': self._elapsed_seconds,
            'retention_rate': (self.kept / self.page_number) if self.page_number > 0 else 0.0,
        }
