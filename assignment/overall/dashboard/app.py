# dashboard/app.py
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
import asyncio, json, os, time, redis

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB   = int(os.getenv("REDIS_DB", "0"))

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

EVENTS_STREAM = "lb:events"
SNAPSHOT_KEY  = "lb:snapshot"

app = FastAPI()

INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>LB Dashboard</title>
  <style>
    body{font-family:Inter,Segoe UI,system-ui,Arial;margin:24px;background:#0b1020;color:#e6e8ef}
    h1,h2{margin:0 0 12px}
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
    .card{background:#121833;border:1px solid #1f2750;border-radius:14px;padding:14px;box-shadow:0 4px 20px rgba(0,0,0,.25)}
    table{border-collapse:collapse;width:100%;font-size:14px}
    th,td{border-bottom:1px solid #1f2750;padding:8px 10px}
    th{color:#9fb0ff;text-align:left}
    .ok{color:#54f29c;font-weight:600}
    .down{color:#ff6b6b;font-weight:600}
    pre{background:#0a0f24;border:1px solid #1f2750;border-radius:12px;padding:10px;height:260px;overflow:auto}
    #meta{margin:6px 0 16px;color:#9fb0ff}
  </style>
</head>
<body>
  <h1>Load Balancer Dashboard</h1>
  <div id="meta"></div>

  <div class="card" style="margin:12px 0;">
    <h2 style="margin-bottom:8px;">Live Topology</h2>
    <svg id="topo" viewBox="0 0 800 260" style="width:100%; height:260px; background:#0a0f24; border:1px solid #1f2750; border-radius:12px;"></svg>
  </div>

  <div class="grid">
    <div>
      <h2>Backends</h2>
      <table id="tbl"><thead><tr><th>Name</th><th>Healthy</th></tr></thead><tbody></tbody></table>
    </div>
    <div>
      <h2>Recent Events</h2>
      <pre id="log"></pre>
    </div>
  </div>

<script>
console.log("[dashboard] JS loaded");

// ------- DOM refs -------
const meta  = document.getElementById('meta');
const tbody = document.querySelector('#tbl tbody');
const log   = document.getElementById('log');
const svg   = document.getElementById('topo');
const gLinks = el("g");  // background links layer
const gDots  = el("g");  // animation dots layer
const gNodes = el("g");  // top layer (rects + labels)
svg.append(gLinks, gDots, gNodes);

// ------- SVG helpers & layout -------
const POS = {
  client: {x: 80,  y: 130, label:"Client"},
  lb:     {x: 240, y: 130, label:"LB"},
  server1:{x: 440, y: 60,  label:"server1"},
  server2:{x: 440, y: 130, label:"server2"},
  server3:{x: 440, y: 200, label:"server3"},
};

const COLOR_MAP = {};

// add near your other globals
const NODE_ELEMS = {}; // id -> { rect, label }

const NODE_TINT = {
  neutralFill:  "#121833",
  neutralStroke:"#1f2750",
  upFill:       "#143725",
  upStroke:     "#54f29c",
  downFill:     "#3a1d1d",
  downStroke:   "#ff6b6b",
};

function setNodeTint(id, status) {
  // only tint server nodes; skip client/LB
  if (!/^server/.test(id) || !NODE_ELEMS[id]) return;
  const { rect } = NODE_ELEMS[id];
  if (status === "up") {
    rect.setAttribute("fill",   NODE_TINT.upFill);
    rect.setAttribute("stroke", NODE_TINT.upStroke);
  } else if (status === "down") {
    rect.setAttribute("fill",   NODE_TINT.downFill);
    rect.setAttribute("stroke", NODE_TINT.downStroke);
  } else {
    rect.setAttribute("fill",   NODE_TINT.neutralFill);
    rect.setAttribute("stroke", NODE_TINT.neutralStroke);
  }
}

function resetServerTints() {
  Object.keys(POS)
    .filter(k => /^server/.test(k))
    .forEach(k => setNodeTint(k, "neutral"));
}


function randomColor() {
  // pleasant bright-ish color
  const hue = Math.floor(Math.random() * 360);
  return `hsl(${hue}, 70%, 60%)`;
}

function colorForClient(cid) {
  if (!COLOR_MAP[cid]) COLOR_MAP[cid] = randomColor();
  return COLOR_MAP[cid];
}

function el(name, attrs={}) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
}
function appendSvg(node, layer = gNodes) {
  layer.appendChild(node);
}
function drawNode(id) {
  const p = POS[id];
  const rect = el("rect", {
    x: p.x - 36, y: p.y - 18, rx: 12, ry: 12,
    width: 72, height: 36, stroke: "#1f2750", fill: "#121833"
  });
  appendSvg(rect, gNodes);

  const label = el("text", {
    x: p.x, y: p.y + 5, "text-anchor": "middle",
    "font-size": "12", fill: "#e6e8ef"
  });
  label.textContent = p.label;
  appendSvg(label, gNodes);

  NODE_ELEMS[id] = { rect, label };
}


function drawLink(a, b) {
  const p1 = POS[a], p2 = POS[b];
  appendSvg(el("line", {
    x1: p1.x + 36, y1: p1.y,
    x2: p2.x - 36, y2: p2.y,
    stroke: "#2a3569", "stroke-width": "2"
  }), gLinks);
}
// ignore hops that are only for dashboard plumbing
const IGNORE_NODES = new Set(["redis", "ui"]);

function animateHopFiltered(a, b, ms=800, color="#9fb0ff") {
  if (IGNORE_NODES.has(a) || IGNORE_NODES.has(b)) return; // skip telemetry hops
  animateHop(a, b, ms, color);
}


function animateHop(a, b, ms = 800, color = "#9fb0ff") {
  if (!POS[a] || !POS[b]) return;
  const p1 = POS[a], p2 = POS[b];
  const dot = el("circle", { r: 5, cx: p1.x + 36, cy: p1.y, fill: color, opacity: "1" });
  gDots.appendChild(dot); 
  const start = performance.now();
  function frame(t) {
    const k = Math.min(1, (t - start) / ms);
    const x = (p1.x + 36) + (p2.x - 36 - (p1.x + 36)) * k;
    const y = p1.y + (p2.y - p1.y) * k;
    dot.setAttribute("cx", x);
    dot.setAttribute("cy", y);
    if (k < 1) requestAnimationFrame(frame);
    else gDots.removeChild(dot);
  }
  requestAnimationFrame(frame);
}

// initial paint
["client","lb","server1","server2","server3"].forEach(drawNode);
[["client","lb"],["lb","server1"],["lb","server2"],["lb","server3"]].forEach(([a,b])=>drawLink(a,b));

// ------- renderers -------
function renderSnapshot(s) {
  if (!s || !s.backends) return;
  const ts = typeof s.ts === "number" ? s.ts : parseFloat(s.ts||0);
  meta.textContent = `Algo: ${s.algo||"?"} | Updated: ${new Date(ts*1000).toLocaleTimeString()}`;

  let backends = [];
  try { backends = JSON.parse(s.backends); } catch(e){ console.warn("Bad backends JSON", s.backends); }

  // --- NEW: reset all server node tints before applying fresh state
  resetServerTints();

  tbody.innerHTML = '';
  backends.forEach(b=>{
    const tr=document.createElement('tr');
    const healthy = !!(b.healthy === true || b.healthy === "true" || b.healthy === 1 || b.healthy === "1");
    tr.innerHTML = `<td>${b.name}</td>
                    <td class="${healthy?'ok':'down'}">${healthy?'UP':'DOWN'}</td>`;
    tbody.appendChild(tr);

    // --- NEW: tint matching server node if it exists in the diagram
    if (POS[b.name]) setNodeTint(b.name, healthy ? "up" : "down");
  });
}


function appendTextEvent(d) {
  if (d.type !== "end") return;
  const backend = d.backend ?? "server?";
  const port    = d.backend_port ?? "????";
  const t = parseFloat(d.ts||0);
  const dur = d.duration_ms ? `${d.duration_ms}ms` : "";
  const up  = d.bytes_up  ? ` ↑${d.bytes_up}`   : "";
  const down= d.bytes_down? ` ↓${d.bytes_down}` : "";
  const line = `[${new Date(t*1000).toLocaleTimeString()}] client ${d.client_peer} -> ${backend}:${port} (${d.algo})  ${dur}${up}${down}`;
  log.textContent = line + "\\n" + log.textContent;
}

function onTypedEvent(d) {
  const cid = d.client_peer ?? "?";
  const color = colorForClient(cid);
  const backend = (d.backend && POS[d.backend]) ? d.backend : null;

  if (d.type === "accept") {
    // Client -> LB
    animateHopFiltered("client", "lb", 600, color);
  } else if (d.type === "connect_ok" && backend) {
    // LB -> chosen backend
    animateHopFiltered("lb", backend, 700, color);
  } else if (d.type === "end" && backend) {
    // Backend -> LB, then LB -> Client
    animateHopFiltered(backend, "lb", 700, color);
    setTimeout(()=>animateHopFiltered("lb", "client", 600, color), 150);
  }

  // Still append the textual log for 'end'
  appendTextEvent(d);
}

// ------- SSE wiring: use NAMED events -------
const es = new EventSource('/stream');
es.addEventListener('snapshot', (ev) => {
  try { const s = JSON.parse(ev.data); renderSnapshot(s); }
  catch(e){ console.error("snapshot parse error", e, ev.data); }
});
es.addEventListener('lb_event', (ev) => {
  try { const d = JSON.parse(ev.data); onTypedEvent(d); }
  catch(e){ console.error("event parse error", e, ev.data); }
});
es.onerror = (e) => console.error("[SSE] error", e);
console.log("[dashboard] SSE connected");
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML

@app.get("/stream")
def stream(last: str = "$"):
    """
    Named SSE events:
      - event: snapshot   data: {...}
      - event: lb_event   data: {...}
    """
    async def event_gen():
        nonlocal last
        while True:
            # 1) snapshot once per second
            try:
                snap = await asyncio.to_thread(r.hgetall, SNAPSHOT_KEY)
                snap = snap or {}
                if "ts" in snap:
                    try:
                        snap["ts"] = float(snap["ts"])
                    except Exception:
                        pass
                yield f"event: snapshot\ndata: {json.dumps(snap)}\n\n"
            except Exception as e:
                # keep the stream alive even if redis hiccups
                yield f": snapshot_error {str(e)}\n\n"

            # 2) new events (long-poll up to 5s) — run in a thread
            try:
                resp = await asyncio.to_thread(r.xread, {EVENTS_STREAM: last}, 100, 5000)
                if resp:
                    _, entries = resp[0]
                    for _id, fields in entries:
                        last = _id
                        item = dict(fields)
                        item["id"] = _id
                        try:
                            item["ts"] = float(item.get("ts", time.time()))
                        except Exception:
                            item["ts"] = time.time()
                        yield f"event: lb_event\ndata: {json.dumps(item)}\n\n"
                else:
                    # idle heartbeat helps some proxies keep the connection open
                    yield ": idle\n\n"
            except Exception as e:
                yield f": xread_error {str(e)}\n\n"

            # small pause between loops
            await asyncio.sleep(1.0)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)
