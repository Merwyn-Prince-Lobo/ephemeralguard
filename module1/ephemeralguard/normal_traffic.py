import time
import random
import hashlib

print("[NORMAL] Payment processor started — simulating legitimate UPI transactions")

def process_payment(amount, card_last4):
    # Simulate normal payment validation
    token = hashlib.sha256(f"card_{card_last4}_{time.time()}".encode()).hexdigest()
    time.sleep(random.uniform(0.1, 0.3))
    return {"status": "approved", "token": token, "amount": amount}

try:
    while True:
        amount = random.randint(100, 50000)
        card = random.randint(1000, 9999)
        result = process_payment(amount, card)
        print(f"[NORMAL] Transaction ₹{amount} card **{card} → {result['status']}")
        time.sleep(random.uniform(0.5, 1.5))
except KeyboardInterrupt:
    print("[NORMAL] Payment processor stopped")
