import os, struct, random

def generate_synthetic_dump(path: str, size_gb: float = 1.0):
    """
    Creates a realistic fake memory dump for testing.
    Matches real memory distribution:
      40% zero pages, 30% low-entropy, 20% high-entropy, 10% structured
    """
    PAGE = 4096
    total_pages = int((size_gb * 1024**3) / PAGE)

    with open(path, 'wb') as f:
        for i in range(total_pages):
            r = random.random()

            if r < 0.40:
                # zero page — should be skipped by triage
                page = b'\x00' * PAGE

            elif r < 0.70:
                # low entropy — file cache, skip
                byte = random.randint(32, 126)
                page = bytes([byte]) * PAGE

            elif r < 0.90:
                # high entropy — crypto keys, heap, keep
                page = os.urandom(PAGE)

            else:
                # structured data — process table, network buffers, keep
                # fake process entry at start of page
                pid   = random.randint(1, 65535)
                page  = struct.pack('>HH', 0xDEAD, pid)  # magic + pid
                page += os.urandom(PAGE - 4)

            f.write(page)

    print(f"Generated {path}: {size_gb}GB, {total_pages} pages")

# Generate a 1GB test dump — takes ~10 seconds
generate_synthetic_dump('test_dump2.bin', size_gb=1.0)