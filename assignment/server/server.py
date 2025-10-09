import os, re, socket
import rpyc
from rpyc.utils.server import ThreadedServer
import redis

DATA_DIR   = os.getenv("DATA_DIR", "/app/data")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB   = int(os.getenv("REDIS_DB", "0"))

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

def count_in_text(text: str, keyword: str) -> int:
    # whole-word, case-insensitive
    return len(re.findall(rf"\b{re.escape(keyword)}\b", text, flags=re.IGNORECASE))

class WordCountService(rpyc.Service):
    def exposed_ping(self):
        return "pong"

    def exposed_count(self, filename: str, keyword: str):
        key = f"count:{filename}:{keyword.lower()}"
        cached = r.get(key)
        if cached is not None:
            r.zincrby("hot_keywords", 1, keyword.lower())
            return {"count": int(cached), "from_cache": True, "server": socket.gethostname()}

        path = os.path.join(DATA_DIR, filename)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        cnt = count_in_text(text, keyword)
        r.set(key, cnt)                                 # cache result
        r.zincrby("hot_keywords", 1, keyword.lower())  # track “hot” keywords
        return {"count": cnt, "from_cache": False, "server": socket.gethostname()}

    def exposed_top_keywords(self, n: int = 5):
        # optional helper to show most requested keywords
        return r.zrevrange("hot_keywords", 0, n-1, withscores=True)

if __name__ == "__main__":
    port = int(os.getenv("SERVICE_PORT", "18861"))
    print(f"[server] starting on 0.0.0.0:{port}")
    ThreadedServer(WordCountService, port=port,
                   protocol_config={"allow_public_attrs": True}).start()
