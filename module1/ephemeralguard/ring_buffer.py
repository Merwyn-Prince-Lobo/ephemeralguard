from collections import deque
import threading
import time

ring_buffer = deque(maxlen=30)
_running = True

def capture_key_regions():
    try:
        with open('/proc/self/smaps', 'r') as f:
            return f.read()
    except Exception as e:
        return f"smaps read error: {e}"

def continuous_snapshot():
    while _running:
        ring_buffer.append({
            'timestamp': time.time(),
            'data': capture_key_regions()
        })
        time.sleep(1)

def start_ring_buffer():
    t = threading.Thread(target=continuous_snapshot, daemon=True)
    t.start()
    print("[RING BUFFER] Started")

def get_buffer_contents():
    return list(ring_buffer)
