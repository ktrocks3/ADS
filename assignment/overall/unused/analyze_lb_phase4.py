# analyze_phase4_compact.py
import os, glob, argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ---- Column name fallbacks ----
TIME_SYNS = ["t_start_utc", "timestamp", "time", "start_time"]
SERVER_SYNS = ["server", "backend", "host"]
LATENCY_SYNS = ["latency_ms", "latency", "lat_ms"]

def find_col(cols, preferred, syns):
    cols_norm = {c.strip().lower(): c for c in cols}
    if preferred and preferred.lower() in cols_norm:
        return cols_norm[preferred.lower()]
    for s in syns:
        if s.lower() in cols_norm:
            return cols_norm[s.lower()]
    raise ValueError(f"Required column not found. Looked for { [preferred] + syns }")

def load_run(path, time_col=None, server_col=None, latency_col=None):
    df = pd.read_csv(path)
    tcol = find_col(df.columns, time_col, TIME_SYNS)
    scol = find_col(df.columns, server_col, SERVER_SYNS)
    lcol = find_col(df.columns, latency_col, LATENCY_SYNS)

    df[tcol] = pd.to_datetime(df[tcol], errors="coerce", utc=True)
    df = df.dropna(subset=[tcol])
    t0 = df[tcol].min()
    df["sec"] = ((df[tcol] - t0).dt.total_seconds()).astype(int).clip(lower=0)

    df[lcol] = pd.to_numeric(df[lcol], errors="coerce")
    df = df.dropna(subset=[lcol]).rename(columns={scol: "server", lcol: "latency_ms"})
    df["server"] = df["server"].astype(str).str.strip().str.lower()
    return df

def infer_label(fname):
    name = os.path.basename(fname).lower()
    algo = "RR" if name.startswith("rr") else ("LC" if name.startswith("lc") else "UNK")
    load = "10C" if "10c" in name else ("1C" if "1c" in name else "UNK")
    return f"{algo}_{load}"

def per_second(df):
    # requests/sec per server
    rps_server = df.groupby(["sec","server"]).size().unstack(fill_value=0).sort_index()
    # p95 latency per second
    p95 = df.groupby("sec")["latency_ms"].quantile(0.95)
    return rps_server, p95

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results_dir", default="../../phase4/results")
    ap.add_argument("--pattern", default="*Results_*.csv")
    ap.add_argument("--out", default="results/figures_compact")
    ap.add_argument("--time_col", default=None)
    ap.add_argument("--server_col", default=None)
    ap.add_argument("--latency_col", default=None)
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.results_dir, args.pattern)))
    if not paths:
        raise SystemExit(f"No CSVs in {args.results_dir} matching {args.pattern}")

    os.makedirs(args.out, exist_ok=True)

    # Load all runs
    runs = []
    for p in paths:
        try:
            df = load_run(p, args.time_col, args.server_col, args.latency_col)
        except Exception as e:
            print(f"!! Skipping {p}: {e}")
            continue
        label = infer_label(p)
        rps_srv, p95 = per_second(df)
        runs.append((label, rps_srv, p95))

    if not runs:
        raise SystemExit("No valid runs loaded.")

    # Normalize labels order to RR_1C, LC_1C, RR_10C, LC_10C where present
    order = ["RR_1C", "LC_1C", "RR_10C", "LC_10C"]
    runs = sorted(runs, key=lambda x: order.index(x[0]) if x[0] in order else 999)

    # -------------------------
    # Figure A: Per-run overview (2x2)
    # -------------------------
    n = len(runs)
    rows = 2
    cols = 2 if n > 1 else 1
    fig, axes = plt.subplots(rows, cols, figsize=(12, 7), squeeze=False)
    idx = 0
    for r in range(rows):
        for c in range(cols):
            if idx >= n:
                axes[r, c].axis("off")
                continue
            label, rps_srv, p95 = runs[idx]
            ax = axes[r, c]

            # Stacked area of per-server RPS
            x = rps_srv.index.values
            if len(rps_srv.columns) == 0:
                ax.text(0.5, 0.5, "No data", ha="center", va="center")
                idx += 1
                continue
            ax.stackplot(x, [rps_srv[col].values for col in rps_srv.columns], labels=rps_srv.columns)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Requests/sec")
            ax.set_title(f"{label}: routing (stacked) + P95 latency")

            # Twin axis for P95 latency
            ax2 = ax.twinx()
            ax2.plot(p95.index.values, p95.values, linewidth=1.5, label="P95 latency", color = "red")
            ax2.set_ylabel("Latency (ms)")

            # Build a compact legend (servers + P95)
            # Put server legend on left, P95 on right
            leg1 = ax.legend(loc="upper left", frameon=False, title="Server")
            ax2.legend(loc="upper right", frameon=False)

            idx += 1

    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "Figure_A_per_run_overview.png"), dpi=180)
    plt.close(fig)

    # -------------------------
    # Figure B: Algo/load comparison (2x1)
    # -------------------------
    # Build combined series aligned by each run's own time base
    # Throughput = sum over servers per second
    thr_lines = []
    p95_lines = []
    labels = []
    for label, rps_srv, p95 in runs:
        thr = rps_srv.sum(axis=1)  # total rps
        thr_lines.append(thr)
        p95_lines.append(p95)
        labels.append(label)

    # Determine common x-limits per line (each run has its own x)
    # We just plot each as-is on its own time base (0..T_run)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), sharex=False)

    for lab, thr in zip(labels, thr_lines):
        ax1.plot(thr.index.values, thr.values, label=lab, linewidth=1.5)
    ax1.set_title("Total throughput over time")
    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("Requests/sec")
    ax1.legend(loc="upper right", ncol=2, frameon=False)

    for lab, p95 in zip(labels, p95_lines):
        ax2.plot(p95.index.values, p95.values, label=lab, linewidth=1.5)
    ax2.set_title("P95 latency over time")
    ax2.set_xlabel("Time (s)")
    ax2.set_ylabel("Latency (ms)")
    ax2.legend(loc="upper right", ncol=2, frameon=False)

    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "Figure_B_algo_load_comparison.png"), dpi=180)
    plt.close(fig)

    print(f"Wrote:\n  {os.path.join(args.out, 'Figure_A_per_run_overview.png')}\n  {os.path.join(args.out, 'Figure_B_algo_load_comparison.png')}")

if __name__ == "__main__":
    main()
