import hashlib
import time
import threading
import socket
import os

# These strings will be visible in the memory dump
ATTACKER_WALLET = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
C2_SERVER = "185.220.101.47"
STOLEN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.STOLEN_SESSION_DATA"
MALICIOUS_PAYLOAD = "INJECTED_CRYPTOMINING_CODE_XMR_MONERO_POOL_CONNECT"

print("[ATTACK] ============================================")
print("[ATTACK] Malicious payload injected into Lambda!")
print(f"[ATTACK] Wallet: {ATTACKER_WALLET}")
print(f"[ATTACK] C2 Server: {C2_SERVER}")
print(f"[ATTACK] Stolen token loaded into memory")
print("[ATTACK] ============================================")

# Keep malicious strings alive in memory throughout execution
attack_data = {
    "wallet": ATTACKER_WALLET,
    "c2": C2_SERVER,
    "token": STOLEN_TOKEN,
    "payload": MALICIOUS_PAYLOAD,
    "exfiltrated_cards": [
        "4532015112830366",
        "4556737586899855",
        "4916338506082832"
    ],
    "credentials": {
        "username": "admin@bank.com",
        "password": "StealThis123!"
    }
}

# CPU spike — cryptomining pattern
def mine_crypto():
    print("[ATTACK] Cryptomining started — CPU spike!")
    end_time = time.time() + 45
    while time.time() < end_time:
        hashlib.sha256(
            f"{ATTACKER_WALLET}{time.time()}".encode()
        ).hexdigest()

# Keep data in memory the whole time
def keep_alive():
    while True:
        _ = str(attack_data)
        time.sleep(0.1)

# Start both threads
t1 = threading.Thread(target=mine_crypto)
t2 = threading.Thread(target=keep_alive, daemon=True)

t1.start()
t2.start()
t1.join()

print("[ATTACK] Attack complete — evidence should be in memory dump")
