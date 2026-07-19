import hashlib
import time
import threading
import socket
import os
from capture import stream_memory_to_s3

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

def contact_c2():
    print(f"[ATTACK] Attempting outbound connection to C2 {C2_SERVER}...")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((C2_SERVER, 443))
    except OSError as e:
        print(f"[ATTACK] C2 connection attempt finished (expected failure): {e}")
    finally:
        s.close()

def mine_crypto():
    print("[ATTACK] Cryptomining started — CPU spike!")
    end_time = time.time() + 45
    while time.time() < end_time:
        hashlib.sha256(
            f"{ATTACKER_WALLET}{time.time()}".encode()
        ).hexdigest()

def keep_alive():
    while True:
        _ = str(attack_data)
        time.sleep(0.1)

t1 = threading.Thread(target=mine_crypto)
t2 = threading.Thread(target=keep_alive, daemon=True)

t1.start()
t2.start()
time.sleep(0.5)
print("[ATTACK] Capturing REAL memory of this process (self) before continuing...")
key, sha = stream_memory_to_s3(trigger_reason='attack_self_capture', pid='self')
if key:
    print(f"[ATTACK] Real dump stored: s3://ephemeralguard-forensics/{key}")
contact_c2()
t1.join()

print("[ATTACK] Attack complete — evidence should be in memory dump")
