
from pathlib import Path
import argparse
import sys
import math
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

REQUIRED_COLUMNS = [
    "pass", "client_id", "t_start_utc", "file", "keyword",
    "count", "from_cache", "server", "latency_ms"
]

def parse_bool(x):
    if isinstance(x, bool):
        return x
    s = str(x).strip().lower()
    if s in {"true","t","1","yes","y"}:
        return True
    if s in {"false","f","0","no","n"}:
        return False
    try:
        return bool(int(s))
    except Exception:
        return bool(s)

def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    df = df.copy()
    df["pass"] = pd.to_numeric(df["pass"], errors="coerce").astype("Int64")
    df["latency_ms"] = pd.to_numeric(df["latency_ms"], errors="coerce")
    df["from_cache"] = df["from_cache"].apply(parse_bool)
    df["t_start_utc"] = pd.to_datetime(df["t_start_utc"], errors="coerce", utc=True)
    df["t_end_utc"] = df["t_start_utc"] + pd.to_timedelta(df["latency_ms"], unit="ms")
    df = df.dropna(subset=["t_start_utc","t_end_utc","latency_ms","pass"])
    return df

def load_dataset(csv_path: Path, clients: int) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = ensure_columns(df)
    df["algo"] = "NoLB"
    df["clients"] = int(clients)
    return df

def percentiles(series: pd.Series, ps=(0.5,0.95,0.99)):
    clean = series.dropna().astype(float).values
    if clean.size == 0:
        return {int(p*100): math.nan for p in ps}
    q = np.quantile(clean, ps, method="linear")
    return {int(p*100): float(v) for p, v in zip(ps, q)}

def compute_basic_metrics(df: pd.DataFrame, tail_q: float = 0.95):
    """
    Returns a dict with overall + per-segment metrics for a single dataset.
    """
    N = len(df)
    clients = int(df["clients"].iloc[0])
    key = f"NoLB-{clients}C"

    t0 = df["t_start_utc"].min()
    t1 = df["t_end_utc"].max()
    duration_s = (t1 - t0).total_seconds() if pd.notnull(t0) and pd.notnull(t1) else math.nan
    throughput = N / duration_s if duration_s and duration_s > 0 else math.nan

    lat = df["latency_ms"].astype(float)
    p = percentiles(lat, (0.5, tail_q, 0.99))
    tail_key = int(tail_q*100)

    # cache split
    hit = df[df["from_cache"] == True]["latency_ms"].astype(float)
    miss = df[df["from_cache"] == False]["latency_ms"].astype(float)
    hp = percentiles(hit, (0.5, tail_q, 0.99)) if len(hit) else {50: math.nan, tail_key: math.nan, 99: math.nan}
    mp = percentiles(miss, (0.5, tail_q, 0.99)) if len(miss) else {50: math.nan, tail_key: math.nan, 99: math.nan}

    return {
        "key": key,
        "algo": "NoLB",
        "clients": clients,
        "requests": N,
        "duration_s": duration_s,
        "throughput_req_s": throughput,
        "lat_mean_ms": float(lat.mean()) if N else math.nan,
        "lat_median_ms": p[50],
        f"lat_p{tail_key}_ms": p[tail_key],
        "lat_p99_ms": p[99],

        "cache_hit_ratio": float(df["from_cache"].mean()) if N else math.nan,

        "hit_mean_ms": float(hit.mean()) if len(hit) else math.nan,
        "hit_p50_ms": hp[50],
        f"hit_p{tail_key}_ms": hp[tail_key],

        "miss_mean_ms": float(miss.mean()) if len(miss) else math.nan,
        "miss_p50_ms": mp[50],
        f"miss_p{tail_key}_ms": mp[tail_key],
    }

def plot_latency_series_per_request(
    df: pd.DataFrame,
    outdir: Path,
    tail_q: float = 0.95,
    title_suffix: str = "",
    show_keywords: bool = False,
):
    """
    Per-request latency series for a single dataset (e.g., 1 client, 40 requests).
    X: request index (1..N), Y: latency_ms.
    Colors: cache hit (filled), cache miss (hollow).
    Overlays mean, p95, p99 (tail).
    """
    if df.empty:
        return None

    # Sort by the time we sent the request from the client
    data = df.sort_values("t_start_utc").reset_index(drop=True).copy()
    data["req_idx"] = np.arange(1, len(data) + 1)

    lat = data["latency_ms"].astype(float).dropna()
    if lat.empty:
        return None

    # Tail stats
    p = percentiles(lat, (0.5, tail_q, 0.99))
    tail_key = int(tail_q * 100)
    mean = float(lat.mean())
    p50  = p[50]
    p_tail = p[tail_key]
    p99  = p[99]

    # Plot
    fig = plt.figure(figsize=(10, 5))
    ax = fig.add_subplot(111)

    # Split by cache segment for visual clarity
    hit = data[data["from_cache"] == True]
    miss = data[data["from_cache"] == False]

    # Connect the points with a thin line (overall) to show temporal evolution
    ax.plot(data["req_idx"], data["latency_ms"], linewidth=0.8, alpha=0.6, label="_nolegend_")

    # Scatter markers: filled for hit, hollow for miss
    if not hit.empty:
        ax.scatter(hit["req_idx"], hit["latency_ms"], s=28, label="cache hit")
    if not miss.empty:
        ax.scatter(miss["req_idx"], miss["latency_ms"], s=28, facecolors="none", edgecolors="black", label="cache miss")

    # Overlays (mean, tail, p99)
    ax.axhline(mean, linestyle="--", linewidth=1.2, label=f"mean = {mean:.1f} ms")
    ax.axhline(p_tail, linestyle="-.", linewidth=1.2, label=f"p{tail_key} = {p_tail:.1f} ms")
    ax.axhline(p99, linestyle=":", linewidth=1.2, label=f"p99 = {p99:.1f} ms")

    ax.set_title(f"Execution latency per request{title_suffix}")
    ax.set_xlabel("Request number (1…N)")
    ax.set_ylabel("Execution latency (ms)")
    ax.legend(loc="best")
    ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.6)
    fig.tight_layout()

    # Optional: show the keyword labels on a top twin axis (gets crowded with 40 labels)
    if show_keywords:
        ax_top = ax.twiny()
        ax_top.set_xlim(ax.get_xlim())
        ax_top.set_xticks(data["req_idx"])
        ax_top.set_xticklabels(data["keyword"], rotation=90, fontsize=7)
        ax_top.set_xlabel("Keyword (by request order)")

    outfile = outdir / f"phase2_latency_series_per_request{title_suffix.replace(' ', '_')}.png"
    fig.savefig(outfile, dpi=150)
    plt.close(fig)

    # Export the per-request table for easy screenshotting/evidence
    cols = ["req_idx", "t_start_utc", "client_id", "keyword", "from_cache", "latency_ms", "file", "count", "pass", "server"]
    data[cols].to_csv(outdir / f"phase2_latency_series_per_request{title_suffix.replace(' ', '_')}.csv", index=False)

    return outfile

def plot_hit_vs_miss_box(df: pd.DataFrame, outdir: Path, title_suffix: str = ""):
    if df.empty: return None
    groups = [df[df["from_cache"]==True]["latency_ms"].dropna().values,
              df[df["from_cache"]==False]["latency_ms"].dropna().values]
    labels = ["hit","miss"]
    fig = plt.figure(figsize=(6,5))
    ax = fig.add_subplot(111)
    ax.boxplot(groups, labels=labels, showfliers=False)
    ax.set_title(f"Latency by cache segment{title_suffix}")
    ax.set_ylabel("Latency (ms)")
    fig.tight_layout()
    outfile = outdir / f"phase2_latency_box_hit_miss{title_suffix.replace(' ', '_')}.png"
    fig.savefig(outfile, dpi=150); plt.close(fig)
    return outfile

def plot_latency_cdf(df: pd.DataFrame, outdir: Path, title_suffix: str = ""):
    if df.empty: return None
    fig = plt.figure(figsize=(7,5))
    ax = fig.add_subplot(111)
    lat = np.sort(df["latency_ms"].dropna().astype(float).values)
    if lat.size == 0: return None
    y = np.arange(1, lat.size+1) / lat.size
    ax.plot(lat, y, label="all")
    ax.set_title(f"Latency CDF{title_suffix}")
    ax.set_xlabel("Latency (ms)")
    ax.set_ylabel("Cumulative probability")
    ax.legend()
    fig.tight_layout()
    outfile = outdir / f"phase2_latency_cdf{title_suffix.replace(' ', '_')}.png"
    fig.savefig(outfile, dpi=150); plt.close(fig)
    return outfile

def print_table(df: pd.DataFrame, title: str):
    print("\n" + "="*len(title))
    print(title)
    print("="*len(title))
    if df.empty:
        print("(no data)"); return
    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 160):
        print(df.to_string(index=False))

def plot_combined_hit_miss_box(all_df: pd.DataFrame, outdir: Path):
    """
    One figure with 9 boxes total:
    (Hit, Miss, Combined) x (1C, 10C, 100C)
    Ensures consistent coloring: Hit = blue, Miss = red, Combined = gray.
    """
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    if all_df.empty:
        return None

    # Use a fixed client order; filter to what's present
    desired = [1, 10, 100]
    clients_all = [c for c in desired if c in set(all_df["clients"].unique())]
    if not clients_all:
        return None

    fig, ax = plt.subplots(figsize=(12, 5))

    spacing = 3.0   # distance between client groups (3 boxes per group)
    width = 0.8     # box width

    # Legend patches (don't rely on returned artists)
    hit_patch = Patch(facecolor="blue",  label="Cache Hit")
    miss_patch = Patch(facecolor="red",   label="Cache Miss")
    comb_patch = Patch(facecolor="white",  label="Combined")

    for i, c in enumerate(clients_all):
        sub = all_df[all_df["clients"] == c]

        # positions within this client group
        base = i * spacing
        pos_hit  = base + 0.0
        pos_miss = base + 1.0
        pos_comb = base + 2.0

        # Extract data
        hit_vals  = sub.loc[sub["from_cache"] == True,  "latency_ms"].dropna().values
        miss_vals = sub.loc[sub["from_cache"] == False, "latency_ms"].dropna().values
        # Combined = all rows for this client (regardless of from_cache)
        comb_vals = sub["latency_ms"].dropna().values

        # Plot HIT
        if hit_vals.size > 0:
            bp_hit = ax.boxplot([hit_vals],
                                positions=[pos_hit],
                                widths=width,
                                showfliers=False,
                                patch_artist=True)
            for box in bp_hit["boxes"]:
                box.set_facecolor("blue")
            for med in bp_hit["medians"]:
                med.set_linewidth(1.5)

        # Plot MISS
        if miss_vals.size > 0:
            bp_miss = ax.boxplot([miss_vals],
                                 positions=[pos_miss],
                                 widths=width,
                                 showfliers=False,
                                 patch_artist=True)
            for box in bp_miss["boxes"]:
                box.set_facecolor("red")
            for med in bp_miss["medians"]:
                med.set_linewidth(1.5)

        # Plot COMBINED
        if comb_vals.size > 0:
            bp_comb = ax.boxplot([comb_vals],
                                 positions=[pos_comb],
                                 widths=width,
                                 showfliers=False,
                                 patch_artist=True)
            for box in bp_comb["boxes"]:
                box.set_facecolor("white")
            for med in bp_comb["medians"]:
                med.set_linewidth(1.5)

    # X-axis: center label under each 3-box cluster
    centers = [i * spacing + 1.0 for i in range(len(clients_all))]
    ax.set_xticks(centers)
    ax.set_xticklabels([f"{c}C" for c in clients_all])

    ax.set_xlabel("Client count")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Cache Hit vs Miss vs Combined Latency across client scales")
    ax.legend(handles=[hit_patch, miss_patch, comb_patch], loc="best")

    fig.tight_layout()
    outfile = Path(outdir) / "phase2_latency_box_all_clients_9boxes.png"
    fig.savefig(outfile, dpi=150)
    plt.close(fig)
    return outfile



def main():
    ap = argparse.ArgumentParser(description="Phase 2 analysis (NoLB single-server).")
    ap.add_argument("--input", type=str, default="../results", help="Folder containing NoLB CSVs.")
    ap.add_argument("--out", type=str, default="./figs2", help="Output folder for figures + CSVs.")
    ap.add_argument("--tail", type=float, default=0.95, help="Tail latency quantile, e.g., 0.95 for p95.")
    ap.add_argument("--series-keywords", type=int, default=5, help="How many top keywords to show in the series plot (min 5).")
    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)

    expected = [
        (1,   in_dir / "NoLB_Results_1C_20T.csv"),
        (10,  in_dir / "NoLB_Results_10C_20T.csv"),
        (100, in_dir / "NoLB_Results_100C_20T.csv"),
    ]
    def find_fallback(clients):
        # If your filenames vary, use any file that matches clients count
        cands = sorted(in_dir.glob(f"NoLB_Results_{clients}C_*.csv"))
        return cands[0] if cands else None

    datasets = []
    for clients, path in expected:
        csv_path = path if path.exists() else find_fallback(clients)
        if not csv_path or not csv_path.exists():
            print(f"[WARN] Missing file for {clients}C at {path}. Skipping.", file=sys.stderr)
            continue
        try:
            df = load_dataset(csv_path, clients)
            datasets.append(df)
        except Exception as e:
            print(f"[ERROR] Failed to load {csv_path}: {e}", file=sys.stderr)

    if not datasets:
        print("[ERROR] No datasets loaded. Check input folder and file names.", file=sys.stderr)
        sys.exit(1)

    all_df = pd.concat(datasets, ignore_index=True)
    all_df = all_df.sort_values(["clients","t_start_utc"]).reset_index(drop=True)

    # Summary metrics per dataset
    rows = []
    for clients, sub in all_df.groupby("clients"):
        rows.append(compute_basic_metrics(sub, tail_q=args.tail))
    summary = pd.DataFrame(rows).sort_values("clients").reset_index(drop=True)

    # Save + print a concise table
    round_cols = {c:3 for c in summary.columns if summary[c].dtype!=object}
    summary_round = summary.round(round_cols)
    summary_round.to_csv(Path(out_dir) / "phase2_summary.csv", index=False)
    print_table(summary_round, "Phase 2 metrics per dataset (NoLB, single server)")

    # === Figures for the report ===
    # 1) Series plot for 1C (clear single-server view)
    df1 = all_df[all_df["clients"] == 1].copy()
    # 2) Per-request latency series for 1 client (40 requests)
    s1 = plot_latency_series_per_request(
        df1, out_dir, tail_q=args.tail, title_suffix=" - 1 client", show_keywords=False
    )

    # 3) Overall CDF for 1C
    c1 = plot_latency_cdf(df1, out_dir, title_suffix=" - 1 client")

    # Optional: also export the same trio for 10C and 100C (handy if you want to mention them in text)
    for c in [10, 100]:
        dfx = all_df[all_df["clients"] == c].copy()
        plot_latency_cdf(dfx, out_dir, title_suffix=f" - {c} clients")

    # Combined boxplot for all client scales (6 boxes: hit/miss × 1C/10C/100C)
    plot_combined_hit_miss_box(all_df, out_dir)

    # Also export per-request raw table for 1C in order for easy screenshotting/evidence
    if not df1.empty:
        # per-request table sorted by time, keeping key fields
        cols = ["t_start_utc","client_id","file","keyword","from_cache","latency_ms","count","pass","server"]
        pr = df1[cols].sort_values("t_start_utc").reset_index(drop=True)
        pr.to_csv(Path(out_dir) / "phase2_per_request_1C.csv", index=False)

if __name__ == "__main__":
    main()