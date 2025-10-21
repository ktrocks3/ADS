import os, asyncio, itertools, contextlib, time, json, secrets, redis

BACKENDS = [("server1", 18861), ("server2", 18861), ("server3", 18861)]

HC_INTERVAL = float(os.getenv("HC_INTERVAL", "2"))   # seconds between checks
HC_TIMEOUT  = float(os.getenv("HC_TIMEOUT", "1"))    # TCP dial timeout
HC_FALL     = int(os.getenv("HC_FALL", "2"))         # mark DOWN after N fails
HC_RISE     = int(os.getenv("HC_RISE", "2"))         # mark UP after N passes

ALGO        = os.getenv("ALGO", "rr").lower()        # 'rr' or 'lc'
LISTEN_PORT = int(os.getenv("LB_PORT", "9000"))
EXTRA_INFO  = bool(int(os.getenv("EXTRA_INFO", "0")))

# Redis (for dashboard)
REDIS_HOST  = os.getenv("REDIS_HOST", "redis")
REDIS_PORT  = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB    = int(os.getenv("REDIS_DB", "0"))

EVENTS_STREAM = "lb:events"   # Redis Stream for events
SNAPSHOT_KEY  = "lb:snapshot" # Redis Hash for periodic snapshot

client_ids = {}

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
health = {b[0]: True  for b in BACKENDS}
fails  = {b[0]: 0     for b in BACKENDS}
passes = {b[0]: 0     for b in BACKENDS}

class Pool:
    def __init__(self, backends):
        self.backends = backends
        self._rr = itertools.cycle(range(len(backends)))
        self.conns = [0] * len(backends)

    async def tcp_check(self, host, port):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=HC_TIMEOUT
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    def pick(self):
        healthy_idxs = [i for i, (h, _) in enumerate(self.backends) if health[h]]
        if not healthy_idxs:
            return None
        if ALGO == "lc":
            return min(healthy_idxs, key=lambda i: self.conns[i])
        # round-robin over healthy
        for _ in range(len(self.backends)):
            idx = next(self._rr)
            if health[self.backends[idx][0]]:
                return idx
        return None

pool = Pool(BACKENDS)

async def health_loop():
    while True:
        checks = []
        for host, port in BACKENDS:
            async def probe(h=host, p=port):
                try:
                    fut = asyncio.open_connection(h, p)
                    rconn, w = await asyncio.wait_for(fut, timeout=HC_TIMEOUT)
                    w.close()
                    with contextlib.suppress(Exception):
                        await w.wait_closed()
                    passes[h] += 1
                    fails[h] = 0
                    if not health[h] and passes[h] >= HC_RISE:
                        health[h] = True
                        print(f"{h} is back online")
                except Exception:
                    fails[h] += 1
                    passes[h] = 0
                    if health[h] and fails[h] >= HC_FALL:
                        health[h] = False
                        print(f"{h} is offline")
            checks.append(asyncio.create_task(probe()))
        with contextlib.suppress(Exception):
            await asyncio.gather(*checks)
        await asyncio.sleep(HC_INTERVAL)

async def snapshot_loop():
    while True:
        snapshot = {
            "ts": time.time(),
            "algo": ALGO,
            "backends": json.dumps([
                {
                    "name": h, "port": p,
                    "healthy": bool(health[h]),
                    "conns": pool.conns[i]
                }
                for i, (h, p) in enumerate(pool.backends)
            ])
        }
        # Hash set: overwrite current snapshot atomically
        r.hset(SNAPSHOT_KEY, mapping=snapshot)
        await asyncio.sleep(1.0)

async def proxy_client(reader, writer):
    tried = set()
    # Per-connection correlation id + client label
    cid = f"{int(time.time()*1000)}-{secrets.token_hex(3)}"
    client_peer = str(writer.get_extra_info("peername")[0])
    if client_peer not in client_ids:
        client_ids[client_peer] = len(client_ids) + 1
    client_peer = client_ids[client_peer]

    if EXTRA_INFO:
        print(f"[LB] New connection from client {client_peer} cid={cid}")

    # hop: client -> LB (accept)
    r.xadd(EVENTS_STREAM, {
        "type": "accept",
        "cid": cid,
        "ts": time.time(),
        "src": "client",
        "dst": "lb",
        "client_peer": client_peer,
        "algo": ALGO
    }, maxlen=2000, approximate=True)

    while True:
        idx = pool.pick()
        if idx is None or idx in tried:
            with contextlib.suppress(Exception):
                writer.write(b"Service unavailable")
                await writer.drain()
                writer.close()
            return

        host, port = pool.backends[idx]
        tried.add(idx)
        if not health[host]:
            continue

        if EXTRA_INFO:
            print(f"[LB] Connecting client -> {host}:{port} (cid={cid})")

        start = time.time()
        bytes_up = 0
        bytes_down = 0
        conns_incremented = False

        try:
            backend_reader, backend_writer = await asyncio.open_connection(host, port)
            pool.conns[idx] += 1
            conns_incremented = True

            # hop: LB -> server (connected OK)
            r.xadd(EVENTS_STREAM, {
                "type": "connect_ok",
                "cid": cid,
                "ts": time.time(),
                "src": "lb",
                "dst": host,
                "backend": host,
                "backend_port": port,
                "algo": ALGO,
                "client_peer": client_peer
            }, maxlen=2000, approximate=True)

            async def pump(src, dst, direction):
                nonlocal bytes_up, bytes_down
                try:
                    while True:
                        data = await src.read(65536)
                        if not data:
                            break
                        dst.write(data)
                        await dst.drain()
                        n = len(data)
                        if direction == "up":
                            bytes_up += n
                        else:
                            bytes_down += n
                finally:
                    with contextlib.suppress(Exception):
                        dst.close()
                        if hasattr(dst, "wait_closed"):
                            await dst.wait_closed()

            await asyncio.gather(
                pump(reader, backend_writer, "up"),
                pump(backend_reader, writer, "down")
            )

            # success: end of this client session; do not try other backends
            break

        except Exception as e:
            if EXTRA_INFO:
                print(f"[LB] Error while proxying via {host}:{port} (cid={cid}): {e}")
            # try another healthy backend in the loop
            continue

        finally:
            if conns_incremented:
                pool.conns[idx] -= 1

            # SINGLE end event with metrics (this drives your text log line)
            r.xadd(EVENTS_STREAM, {
                "type": "end",
                "cid": cid,
                "ts": time.time(),
                "client_peer": client_peer,
                "backend": host,
                "backend_port": port,
                "algo": ALGO,
                "duration_ms": int((time.time() - start) * 1000),
                "bytes_up": bytes_up,
                "bytes_down": bytes_down
            }, maxlen=2000, approximate=True)

async def main():
    server = await asyncio.start_server(proxy_client, host="0.0.0.0", port=LISTEN_PORT)
    print(f"[GUI LB] listening on :{LISTEN_PORT}  ALGO={ALGO}  redis={REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}")
    # Run health checks + snapshots
    asyncio.create_task(health_loop())
    asyncio.create_task(snapshot_loop())
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
