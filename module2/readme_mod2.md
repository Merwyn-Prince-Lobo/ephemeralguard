# Module 2 – Memory Triage & Integrity Verification

## Overview

Module 2 is responsible for processing a captured process memory dump after a serverless function has been flagged as suspicious.

Its objectives are to:

* Reduce the size of the memory dump by filtering out pages that are unlikely to contain useful forensic evidence.
* Preserve the integrity of retained memory pages using SHA-256 hashing.
* Generate integrity metadata for later verification.
* Produce artifacts that can be securely transferred and stored by Module 3.

> **Note:** Memory acquisition and attack detection are outside the scope of this module and are handled by other project components.

---

# Features

## 1. Page-Based Memory Triage

The memory dump is processed one 4 KB page at a time.

Current filtering methods include:

* Zero-page detection
* Context-aware weighted forensic page scoring (see below)

Pages identified as empty or containing extremely low information content are discarded. The previous binary "entropy > threshold" decision has been replaced by a weighted score that combines entropy, forensic keyword indicators, executable signature detection, and (optionally) attack context — the page-drop policy for low-value, signal-free pages is otherwise unchanged.

---

## 2. Weighted Forensic Page Scoring

Every non-zero page receives a total forensic score built from independent, modular components:

* **Entropy score (0–40)** — NumPy-based Shannon entropy, normalized onto a 0–40 scale. Higher entropy contributes more.
* **Keyword score (0–40)** — the page is decoded (UTF-8, errors ignored) and searched for forensic indicators, grouped into modular categories: **credentials** (`password`, `token`, `cookie`, `session`, ...), **cloud** (`aws_secret_access_key`, `access_key`, ...), **ssh** (`begin private key`, `ssh-rsa`, `id_rsa`, ...), and **database** (`mysql`, `postgres`, `mongodb`, `redis`). Each category has its own weighted keyword table and its own scoring function; matches are combined and capped at 40 so no single term can dominate the score.
* **Executable signature score (0–20)** — detects ELF/PE (`MZ`) headers.
* **Context score (0–20, optional)** — see "Context-Aware Scoring" below. Contributes 0 when no context is supplied, so scoring is unchanged from the non-context-aware baseline in that case.

The total score determines the page's category (`ZERO_PAGE` / `LOW_VALUE` / `MEDIUM_VALUE` / `HIGH_VALUE`) and, in turn, whether the page is retained. Each retained page's metadata also carries an **evidence confidence** percentage derived from its total score.

Implementation lives in `traige_classifier.py`, kept modular via single-responsibility helper functions: `calculate_entropy()`, `entropy_score()`, `keyword_score()` (plus one function per keyword category), `signature_score()`, `context_score()`, `calculate_total_score()`, `classify_page()`, and `confidence_score()`.

---

## 3. Context-Aware Scoring

The triage classifier can optionally be given an attack/trigger **context** (e.g. `"credential_theft"`, `"cryptomining"`, `"network_exfiltration"`) without any change to downstream modules or the pipeline architecture. This does **not** integrate Falco directly — it's a plain string the extractor accepts and uses to adjust its own scoring weights.

* If no context is provided, scoring behaves exactly as it did before context-awareness was added.
* If a context is provided, indicators relevant to that context receive extra score (capped at 20 points), on top of the baseline entropy/keyword/signature score. For example, `"cryptomining"` boosts miner-related indicators (`xmrig`, `minerd`, `cpuminer`) and adds a bonus when an executable signature is also present on the page.
* Each context is implemented as its own small scoring function registered in a lookup table (`CONTEXT_SCORERS`), so adding a future context requires no changes to existing logic.

Every retained page's metadata records which context (if any) was used: `context_used`.

---

## 4. Entropy Calculation

Entropy is calculated for every non-zero memory page as one component of the weighted forensic score described above.

Current implementation:

* NumPy-based entropy calculation
* Configurable low-value-page entropy threshold (drop policy for signal-free pages)
* Fast page-by-page processing
* Keyword-based, executable-signature-based, and (optional) context-based scoring alongside entropy

---

## 5. SHA-256 Integrity Verification

Every retained page is hashed using SHA-256.

The generated hash is used to verify that evidence has not been modified after acquisition or during transfer.

---

## 6. Manifest Generation

For every retained page, a manifest entry is created containing:

* Page number
* Entropy value
* SHA-256 hash

The manifest is stored as:

```
manifest.json
```

This file is later used to verify the integrity of uploaded forensic evidence.

Per-page metadata yielded by the extractor (available to any downstream consumer, e.g. for a richer manifest) now also includes: `category`, `score`, `confidence`, `keyword_matches`, `keyword_score`, `entropy_score`, `signature_score`, and `context_used`, in addition to the original `page_number`, `entropy`, `sha256`, `page_bytes`, `category`, `score`, `keyword_matches`, and `executable_header` fields. No existing field was removed or renamed.

---

## 7. Merkle Tree Generation

SHA-256 hashes from all retained pages are combined into a Merkle Tree.

The resulting Merkle Root provides a single integrity value representing the entire evidence set.

This allows efficient verification that no retained page has been altered.

---

# Processing Pipeline

```
Memory Dump
      │
      ▼
Read 4 KB Page
      │
      ▼
Zero Page Detection
      │
      ▼
Entropy Calculation
      │
      ▼
Useful?
 ┌────┴────┐
 │         │
No        Yes
 │         │
Drop   SHA-256 Hash
           │
           ▼
      Manifest Entry
           │
           ▼
      Merkle Tree
           │
           ▼
Output Artifacts
```

---

# Output Files

The module currently produces:

```
manifest.json
```

Contains:

* Page number
* Entropy score
* SHA-256 hash

Future output:

```
retained_pages.bin
```

Contains only the retained memory pages.

Future output:

```
merkle_root.txt
```

Contains the final Merkle Root for integrity verification.

---

# Performance Metrics

The module reports (via `stats()`):

* Total pages processed
* Pages retained
* Pages discarded
* Drop rate (`drop_rate_pct`)
* Zero pages / low-value pages / medium-value pages / high-value pages
* Average entropy
* Average forensic score
* Processing time (`processing_time`)
* Retention rate (`retention_rate`)

---

# Integration

Module 2 receives:

```
Memory Dump
```

from:

* Module 1 (Memory Acquisition)

Module 2 sends:

* Retained memory pages
* manifest.json
* Merkle Root

to:

* Module 3 (Secure Transfer & Cloud Storage)

---

# Current Status

Implemented:

* Page-by-page processing
* Zero-page detection
* NumPy entropy calculation
* Entropy-based triage
* SHA-256 page hashing
* Manifest generation
* Merkle Root generation
* Performance statistics

Planned:

* Secure transfer integration with Module 3
* Upload retained pages to KMS-encrypted S3 storage
* Integrity verification after upload
* Validation using synthetic memory dumps

---

# Testing

Current testing uses a synthetic memory dump generator that simulates realistic memory page distributions, including:

* Zero pages
* Low-entropy pages
* High-entropy pages
* Structured pages

The expected triage result is approximately:

* 70% pages discarded
* 30% pages retained

This validates the functionality of the triage classifier before integration with the complete forensic acquisition pipeline.

---

# Changelog — `traige_classifier.py` (context-aware scoring)

The pipeline architecture, module boundaries, `TriageStreamingExtractor` public interface, and downstream contracts (`ChunkStreamer`, `SecureTransferModule`) are unchanged. This is a drop-in replacement inside `module2/` only. Naming changes within `traige_classifier.py`:

| Old name | Status | New name |
|---|---|---|
| `normalize_entropy_score()` | kept as alias | `entropy_score()` (new canonical name) |
| `compute_keyword_score(page: bytes)` | kept, now a thin wrapper | `keyword_score(page_text: str)` (new canonical name; operates on pre-decoded text and also returns per-category scores) |
| `categorize_score()` | kept as alias | `classify_page()` (new canonical name) |
| *(none — new)* | added | `decode_page_text()` |
| *(none — new)* | added | `credential_keyword_score()`, `cloud_keyword_score()`, `ssh_keyword_score()`, `database_keyword_score()` (one function per keyword category, registered in `KEYWORD_CATEGORY_SCORERS`) |
| *(none — new)* | added | `signature_score()` |
| *(none — new)* | added | `context_score()` (with per-context helpers registered in `CONTEXT_SCORERS`) |
| *(none — new)* | added | `calculate_total_score()` |
| *(none — new)* | added | `confidence_score()` |

`TriageStreamingExtractor.__init__()` gained one new optional parameter, `context: Optional[str] = None`, appended after the existing `threshold` parameter — existing call sites (`TriageStreamingExtractor(filename)` or `TriageStreamingExtractor(filename, threshold)`) require no changes.
