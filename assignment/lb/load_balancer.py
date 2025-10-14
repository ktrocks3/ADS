import os
import asyncio
import itertools
import contextlib

BACKENDS = [ ("server1", 18861),
    ("server2", 18861),
    ("server3", 18861),
]

ALGO = os.getenv("ALGO", "rr").lower()   # 'rr' or 'lc'
LISTEN_PORT = int(os.getenv("LB_PORT", "9000"))

class Pool:
    def __init__(self, backends):
        self.backends = backends
        self._rr = itertools.cycle(range(len(backends)))
        self.conns = [0] * len(backends)

    def pick(self):
        if ALGO == "lc":
            return min(range(len(self.backends)), key=lambda i: self.conns[i])
        return next(self._rr)

pool = Pool(BACKENDS)

async def proxy_client(reader, writer):
    idx = pool.pick()
    host, port = pool.backends[idx]
    pool.conns[idx] += 1
    try:
        backend_reader, backend_writer = await asyncio.open_connection(host, port)

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
    except Exception:
        with contextlib.suppress(Exception):
            writer.close()
    finally:
        pool.conns[idx] -= 1

async def main():
    server = await asyncio.start_server(proxy_client, host="0.0.0.0", port=LISTEN_PORT)
    print(f"Load balancer listening on :{LISTEN_PORT}  ALGO={ALGO}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())