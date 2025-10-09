import ast, os, time, rpyc, csv, socket, random, datetime

host = os.getenv("SERVER_HOST", "server")
port = int(os.getenv("RPYC_PORT", "18861"))
CLIENT_ID = os.getenv("CLIENT_ID") or socket.gethostname()  # unique per container
print(f"{CLIENT_ID} has started")
START_JITTER = 10
time.sleep(random.random() * START_JITTER)
CONNECT_EACH = True
TWO_PASSES   = False

# Load random tests
with open("word_list") as f:
    options = f.readlines()
tests = [ast.literal_eval(random.choice(options).strip()) for _ in range(5)]

def run_batch(passno: int):
    rows = []
    if CONNECT_EACH:
        for i, (fname, kw) in enumerate(tests, 1):
            conn = rpyc.connect(host, port)
            try:
                t_start = datetime.datetime.utcnow().isoformat()
                t0 = time.perf_counter()
                resp = conn.root.count(fname, kw)  # resp may be a netref dict
                dt_ms = (time.perf_counter() - t0) * 1000.0

                # Use resp WHILE conn is still open:
                print(
                    f"client={CLIENT_ID} pass={passno} req={i}/{len(tests)} "
                    f"file={fname:6s} kw={kw:8s} count={resp['count']:5d} "
                    f"cache={resp['from_cache']} server={resp['server']} "
                    f"latency={dt_ms:.2f}ms",
                    flush=True
                )
                rows.append([passno, CLIENT_ID, t_start, fname, kw,
                             resp['count'], resp['from_cache'], resp['server'], dt_ms])
            finally:
                conn.close()
    else:
        conn = rpyc.connect(host, port)
        try:
            for i, (fname, kw) in enumerate(tests, 1):
                t_start = datetime.datetime.utcnow().isoformat()
                t0 = time.perf_counter()
                resp = conn.root.count(fname, kw)
                dt_ms = (time.perf_counter() - t0) * 1000.0
                print(
                    f"client={CLIENT_ID} pass={passno} req={i}/{len(tests)} "
                    f"file={fname:6s} kw={kw:8s} count={resp['count']:5d} "
                    f"cache={resp['from_cache']} server={resp['server']} "
                    f"latency={dt_ms:.2f}ms",
                    flush=True
                )
                rows.append([passno, CLIENT_ID, t_start, fname, kw,
                             resp['count'], resp['from_cache'], resp['server'], dt_ms])
        finally:
            conn.close()
    return rows


all_rows = run_batch(1)
if TWO_PASSES:
    all_rows += run_batch(2)

# Write one CSV per client container
if os.getenv("SAVE_CSV", "1") == "1":
    os.makedirs("/results", exist_ok=True)
    outfile = f"/results/latency-{CLIENT_ID}.csv"
    with open(outfile, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pass","client_id","t_start_utc","file","keyword",
                    "count","from_cache","server","latency_ms"])
        w.writerows(all_rows)
    print(f"wrote {outfile}", flush=True)
