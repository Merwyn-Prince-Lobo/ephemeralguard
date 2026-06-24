import numpy as np
import hashlib
import json
import time

PAGE_SIZE = 4096
ENTROPY_THRESHOLD = 1.0

manifest = []
page_hashes = []
entropy_values = []


def calculate_entropy(page):
    arr = np.frombuffer(page, dtype=np.uint8)

    counts = np.bincount(arr, minlength=256)
    probs = counts[counts > 0] / arr.size

    return float(-np.sum(probs * np.log2(probs)))


def classify_page(page, threshold=ENTROPY_THRESHOLD):
    arr = np.frombuffer(page, dtype=np.uint8)

    # Zero-page detection
    if not arr.any():
        return False, 0.0

    entropy = calculate_entropy(page)

    if entropy < threshold:
        return False, entropy

    return True, entropy


def sha256_page(page):
    return hashlib.sha256(page).digest()


def build_merkle_root(hashes):
    if not hashes:
        return None

    current = hashes[:]

    while len(current) > 1:

        if len(current) % 2:
            current.append(current[-1])

        next_level = []

        for i in range(0, len(current), 2):
            combined = current[i] + current[i + 1]

            next_hash = hashlib.sha256(combined).digest()

            next_level.append(next_hash)

        current = next_level

    return current[0].hex()


def process_dump(filename):

    kept = 0
    dropped = 0
    page_number = 0

    start = time.perf_counter()

    with open(filename, "rb") as f:

        while True:

            page = f.read(PAGE_SIZE)

            if not page:
                break

            keep, entropy = classify_page(page)

            if keep:

                page_hash = sha256_page(page)

                page_hashes.append(page_hash)

                manifest.append(
                    {
                        "page_number": page_number,
                        "entropy": round(entropy, 4),
                        "sha256": page_hash.hex()
                    }
                )

                entropy_values.append(entropy)

                kept += 1

            else:
                dropped += 1

            page_number += 1

    elapsed = time.perf_counter() - start

    merkle_root = build_merkle_root(page_hashes)

    with open("manifest.json", "w") as mf:
        json.dump(manifest, mf, indent=2)

    print("\n========== RESULTS ==========")
    print(f"Pages Processed : {page_number:,}")
    print(f"Pages Kept      : {kept:,}")
    print(f"Pages Dropped   : {dropped:,}")
    print(f"Drop Rate       : {(dropped/page_number)*100:.2f}%")

    if entropy_values:
        print(f"Average Entropy : {np.mean(entropy_values):.4f}")

    print(f"Merkle Root     : {merkle_root}")
    print(f"Processing Time : {elapsed:.2f}s")
    print(f"Pages/Second    : {page_number/elapsed:.2f}")

    print("\nManifest saved to manifest.json")


if __name__ == "__main__":
    process_dump("test_dump.bin")