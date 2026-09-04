import boto3, time, subprocess, statistics as st, sys
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
B = "ephemeralguard-forensics"
s3 = boto3.client("s3", endpoint_url="http://localhost:4566",
    aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")

def enc_keys():
    out, kw = set(), dict(Bucket=B, Prefix="dumps/")
    while True:
        r = s3.list_objects_v2(**kw)
        out |= {o["Key"] for o in r.get("Contents", []) if o["Key"].endswith(".enc")}
        if not r.get("IsTruncated"): return out
        kw["ContinuationToken"] = r["NextContinuationToken"]

res = []
for i in range(N):
    before = enc_keys()
    t0 = time.perf_counter()
    subprocess.run(["docker","exec","-d","victim","python3","/ephemeralguard/attack_simulation.py"], check=True)
    while not (enc_keys() - before):
        time.sleep(0.02)
        if time.perf_counter() - t0 > 30: break
    ms = (time.perf_counter() - t0) * 1000
    res.append(ms); print(f"{i+1:02d}: {ms:.0f} ms")
    time.sleep(8)
res.sort()
print(f"\nn={len(res)} mean={st.mean(res):.0f} median={st.median(res):.0f} "
      f"p95={res[max(0,int(.95*len(res))-1)]:.0f} max={res[-1]:.0f} stdev={st.stdev(res):.0f} (ms)")
