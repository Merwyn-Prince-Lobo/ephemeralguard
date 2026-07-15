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
* Entropy-based page classification using NumPy

Pages identified as empty or containing extremely low information content are discarded.

---

## 2. Entropy Calculation

Entropy is calculated for every non-zero memory page.

The entropy score is used to estimate how much information is contained within the page.

Current implementation:

* NumPy-based entropy calculation
* Configurable entropy threshold
* Fast page-by-page processing
* added  key word based classification

---

## 3. SHA-256 Integrity Verification

Every retained page is hashed using SHA-256.

The generated hash is used to verify that evidence has not been modified after acquisition or during transfer.

---

## 4. Manifest Generation

For every retained page, a manifest entry is created containing:

* Page number
* Entropy value
* SHA-256 hash

The manifest is stored as:

```
manifest.json
```

This file is later used to verify the integrity of uploaded forensic evidence.

---

## 5. Merkle Tree Generation

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

The module reports:

* Total pages processed
* Pages retained
* Pages discarded
* Drop rate
* Average entropy
* Processing time
* Processing throughput (pages/second)

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
