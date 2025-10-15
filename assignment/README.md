# Word Count Service

A distributed word count system using Redis, multiple servers, a load balancer, and clients (with optional GUI).

---

## Run Instructions
### Clear redis cache
```bash
docker compose exec redis redis-cli FLUSHALL
```
### Build & Run (No GUI)
```bash
docker compose up --build
````

### Run with Multiple Clients

```bash
docker compose up --build --scale client=5
```

### Run with GUI

Make sure in `docker-compose.yml` under **lb**:

```yaml
- ENABLE_GUI=1
```

Then run:

```bash
docker compose --profile gui up --build --scale client=2
```

---

## Services

| Service   | Description                                          |
| --------- | ---------------------------------------------------- |
| redis     | Key-value datastore                                  |
| server1–3 | Word-count servers                                   |
| lb        | Load balancer (`ENABLE_GUI=1` for GUI mode)          |
| client    | Sends requests (can scale)                           |
| dashboard | GUI dashboard (port 8080, only with `--profile gui`) |

---

## Results

Client logs and latency CSVs are saved in:

```
./results/
```

---

## Useful Commands

| Task           | Command                           |
| -------------- | --------------------------------- |
| Stop all       | `docker compose down`             |
| Rebuild images | `docker compose build --no-cache` |
| View logs      | `docker compose logs -f client`   |

---

**Ports**

* Load Balancer: `http://localhost:9000`
* Dashboard (GUI): `http://localhost:8080`
