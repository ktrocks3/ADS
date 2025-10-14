import os
import asyncio
import itertools
import contextlib

BACKENDS = [("server1", 18861), ("server2", 18861), ("server3", 18861)]

health = {b[0]: True  for b in BACKENDS}
fails  = {b[0]: 0     for b in BACKENDS}
passes = {b[0]: 0     for b in BACKENDS}

HC_INTERVAL = float(os.getenv("HC_INTERVAL", "2"))  # seconds between checks
HC_TIMEOUT = float(os.getenv("HC_TIMEOUT", "1"))  # TCP dial timeout
HC_FALL = int(os.getenv("HC_FALL", "2"))  # mark DOWN after N fails
HC_RISE = int(os.getenv("HC_RISE", "2"))

ALGO = os.getenv("ALGO", "rr").lower()  # 'rr' or 'lc'
LISTEN_PORT = int(os.getenv("LB_PORT", "9000"))
EXTRA_INFO = bool(int(os.getenv("EXTRA_INFO", "0")))




class Pool:
    def __init__(self, backends):
        self.backends = backends
        self._rr = itertools.cycle(range(len(backends)))
        self.conns = [0] * len(backends)

    async def tcp_check(self, host, port):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=HC_TIMEOUT
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
                    r, w = await asyncio.wait_for(fut, timeout=HC_TIMEOUT)
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
        # let all probes finish; then sleep
        with contextlib.suppress(Exception):
            await asyncio.gather(*checks)
        await asyncio.sleep(HC_INTERVAL)

async def proxy_client(reader, writer):
    tried = set()
    while True:
        if EXTRA_INFO:
            print(f"[LB] New connection from client {writer.get_extra_info('peername')}")
        idx = pool.pick()
        host, port = pool.backends[idx]
        if EXTRA_INFO:
            print(f"[LB] Connecting client -> {host}:{port}")

        if idx is None or idx in tried:
            with contextlib.suppress(Exception):
                # No one is healthy
                writer.write(b"Service unavailable")
                await writer.drain()
                writer.close()
            return

        tried.add(idx)
        if not health[host]:
            continue
        try:
            backend_reader, backend_writer = await asyncio.open_connection(host, port)
            if EXTRA_INFO:
                print(f"[LB] Connection established: {host}:{port}")
            pool.conns[idx] += 1

            async def pump(src, dst):
                try:
                    while True:
                        data = await src.read(65536)
                        if not data:
                            break
                        dst.write(data)
                        await dst.drain()
                finally:
                    with contextlib.suppress(Exception):
                        dst.close()

            await asyncio.gather(pump(reader, backend_writer), pump(backend_reader, writer))
            break

        except Exception:
            continue
        finally:
            pool.conns[idx] -= 1


async def main():
    server = await asyncio.start_server(proxy_client, host="0.0.0.0", port=LISTEN_PORT)
    print(f"Load balancer with failure detection listening on :{LISTEN_PORT}  ALGO={ALGO}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
