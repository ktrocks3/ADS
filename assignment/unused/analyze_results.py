from pathlib import Path
import argparse
import sys
import math
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

REQUIRED_COLUMNS = [
    "pass","client_id","t_start_utc","file","keyword","count","from_cache","server","latency_ms"
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

def load_dataset(csv_path: Path, algo: str, clients: int) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = ensure_columns(df)
    df["algo"] = algo
    df["clients"] = int(clients)
    return df

def percentiles(series: pd.Series, ps=(0.5,0.9,0.95,0.99)):
    clean = series.dropna().astype(float).values
    if clean.size == 0:
        return {int(p*100): math.nan for p in ps}
    q = np.quantile(clean, ps, method="linear")
    return {int(p*100): v for p, v in zip(ps, q)}

def gini_coefficient(counts: np.ndarray) -> float:
    x = np.sort(np.asarray(counts, dtype=float))
    if x.size == 0:
        return math.nan
    if np.all(x == 0):
        return 0.0
    n = x.size
    cumx = np.cumsum(x)
    gini = (n + 1 - 2 * np.sum(cumx) / cumx[-1]) / n
    return float(gini)

def dataset_key(algo, clients):
    return f"{algo}-{clients}C"

def compute_dataset_metrics(df: pd.DataFrame):
    """Compute metrics for a single (algo, clients) dataset, plus per-cache-segment (hit/miss)."""
    N = len(df)
    clients = int(df["clients"].iloc[0])
    algo = str(df["algo"].iloc[0])
    key = dataset_key(algo, clients)

    t0 = df["t_start_utc"].min()
    t1 = df["t_end_utc"].max()
    duration_s = (t1 - t0).total_seconds() if pd.notnull(t0) and pd.notnull(t1) else math.nan
    throughput = N / duration_s if duration_s and duration_s > 0 else math.nan

    lat = df["latency_ms"]
    p = percentiles(lat, (0.5,0.9,0.95,0.99))

    cache_hit_ratio = float(df["from_cache"].mean()) if N > 0 else math.nan

    # Cache hit vs miss sub-metrics
    seg_rows = []
    per_server_latency_by_seg = []
    server_dist_by_seg = {}
    for seg_bool, seg_name in [(True, "hit"), (False, "miss")]:
        sub = df[df["from_cache"] == seg_bool]
        n = len(sub)
        if n == 0:
            seg_rows.append({
                "algo": algo, "clients": clients, "segment": seg_name,
                "requests": 0, "duration_s": math.nan, "throughput_req_s": math.nan,
                "lat_mean_ms": math.nan, "lat_p50_ms": math.nan,
                "lat_p95_ms": math.nan, "lat_p99_ms": math.nan,
            })
            server_dist_by_seg[seg_name] = pd.Series(dtype=int)
            continue

        t0s = sub["t_start_utc"].min()
        t1s = sub["t_end_utc"].max()
        durs = (t1s - t0s).total_seconds() if pd.notnull(t0s) and pd.notnull(t1s) else math.nan
        thrs = n / durs if durs and durs > 0 else math.nan
        pp = percentiles(sub["latency_ms"])

        seg_rows.append({
            "algo": algo, "clients": clients, "segment": seg_name,
            "requests": n, "duration_s": durs, "throughput_req_s": thrs,
            "lat_mean_ms": float(sub["latency_ms"].mean()),
            "lat_p50_ms": pp[50], "lat_p95_ms": pp[95], "lat_p99_ms": pp[99],
        })

        # Per-server latency by segment
        psl = (sub.groupby("server")["latency_ms"]
               .agg(lat_mean_ms="mean", lat_p95_ms=lambda s: np.quantile(s, 0.95))
               .reset_index())
        psl["algo"] = algo
        psl["clients"] = clients
        psl["segment"] = seg_name
        per_server_latency_by_seg.append(psl)

        server_dist_by_seg[seg_name] = sub["server"].value_counts().sort_index()

    per_segment_df = pd.DataFrame(seg_rows)

    # Server distribution and fairness overall
    server_counts = df["server"].value_counts().sort_index()
    sd = float(server_counts.std(ddof=0)) if len(server_counts) > 0 else math.nan
    mu = float(server_counts.mean()) if len(server_counts) > 0 else math.nan
    fairness_index = 1.0 - (sd/mu) if mu and mu > 0 else math.nan
    gini = gini_coefficient(server_counts.values) if len(server_counts) > 0 else math.nan

    # Per-server latency overall
    per_server_latency_all = (
        df.groupby("server")["latency_ms"]
        .agg(lat_mean_ms="mean", lat_p95_ms=lambda s: np.quantile(s, 0.95))
        .reset_index()
    )
    per_server_latency_all["algo"] = algo
    per_server_latency_all["clients"] = clients
    per_server_latency_all["segment"] = "all"

    summary = {
        "key": key,
        "algo": algo,
        "clients": clients,
        "requests": N,
        "unique_clients": df["client_id"].nunique(),
        "duration_s": duration_s,
        "throughput_req_s": throughput,
        "lat_mean_ms": float(lat.mean()) if N>0 else math.nan,
        "lat_median_ms": float(p[50]) if 50 in p else math.nan,
        "lat_p95_ms": float(p[95]) if 95 in p else math.nan,
        "lat_p99_ms": float(p[99]) if 99 in p else math.nan,
        "lat_min_ms": float(lat.min()) if N>0 else math.nan,
        "lat_max_ms": float(lat.max()) if N>0 else math.nan,
        "lat_std_ms": float(lat.std(ddof=0)) if N>0 else math.nan,
        "cache_hit_ratio": cache_hit_ratio,
        "cache_hit_mean_lat_ms": float(df.loc[df["from_cache"]==True, "latency_ms"].mean()) if (df["from_cache"]==True).any() else math.nan,
        "cache_miss_mean_lat_ms": float(df.loc[df["from_cache"]==False, "latency_ms"].mean()) if (df["from_cache"]==False).any() else math.nan,
        "server_count": int(len(server_counts)),
        "server_fairness_index": fairness_index,
        "server_gini": gini,
    }

    per_server_latency = pd.concat([per_server_latency_all] + per_server_latency_by_seg, ignore_index=True)

    return summary, per_segment_df, server_counts, per_server_latency, server_dist_by_seg

def print_table(df: pd.DataFrame, title: str):
    print("\\n" + "="*len(title))
    print(title)
    print("="*len(title))
    if df.empty:
        print("(no data)")
        return
    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 160):
        print(df.to_string(index=False))

def plot_server_distribution(server_counts: pd.Series, algo: str, clients: int, outdir: Path, suffix: str = ""):
    import numpy as np
    fig = plt.figure(figsize=(8,5))
    ax = fig.add_subplot(111)
    servers = server_counts.index.astype(str).tolist()
    counts = server_counts.values.tolist()
    xpos = np.arange(len(servers))
    ax.bar(xpos, counts)
    if suffix:
        title_suffix = f" ({suffix})"
        file_suffix = f"_{suffix}"
    else:
        title_suffix = ""
        file_suffix = ""
    ax.set_title(f"Server request distribution - {algo}, {clients} clients{title_suffix}")
    ax.set_xlabel("Server")
    ax.set_ylabel("Requests handled")
    ax.set_xticks(xpos)
    ax.set_xticklabels(servers, rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(outdir / f"server_distribution_{algo}_{clients}C{file_suffix}.png", dpi=150)
    plt.close(fig)

def plot_latency_box_by_cache(all_df: pd.DataFrame, algo: str, clients: int, outdir: Path):
    sub = all_df[(all_df["algo"]==algo) & (all_df["clients"]==clients)]
    if sub.empty:
        return
    groups = [sub[sub["from_cache"]==True]["latency_ms"].dropna().values,
              sub[sub["from_cache"]==False]["latency_ms"].dropna().values]
    labels = ["hit","miss"]
    fig = plt.figure(figsize=(7,5))
    ax = fig.add_subplot(111)
    ax.boxplot(groups, labels=labels, showfliers=False)
    ax.set_title(f"Latency by cache segment - {algo}, {clients} clients")
    ax.set_ylabel("Latency (ms)")
    fig.tight_layout()
    fig.savefig(outdir / f"latency_box_cache_{algo}_{clients}C.png", dpi=150)
    plt.close(fig)

def plot_combined_latency_box_by_cache(all_df: pd.DataFrame, outdir: Path):
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    # --- determine available algorithms and clients ---
    if "algo" not in all_df.columns or "clients" not in all_df.columns:
        return
    algos = sorted(all_df["algo"].unique())
    clients_all = sorted(all_df["clients"].unique(), key=int)

    # --- instead of (algo-major), interleave by clients ---
    combos = []
    for clients in clients_all:
        for algo in algos:
            sub = all_df[(all_df["algo"] == algo) & (all_df["clients"] == clients)]
            if not sub.empty:
                combos.append((algo, int(clients)))

    # --- collect data ---
    hit_series, miss_series, labels = [], [], []
    for algo, clients in combos:
        sub = all_df[(all_df["algo"] == algo) & (all_df["clients"] == clients)]
        if sub.empty:
            continue
        h = sub.loc[sub["from_cache"] == True, "latency_ms"].dropna().values
        m = sub.loc[sub["from_cache"] == False, "latency_ms"].dropna().values
        if h.size == 0 and m.size == 0:
            continue
        hit_series.append(h)
        miss_series.append(m)
        labels.append(f"{algo}-{clients}C")

    if not labels:
        return

    fig = plt.figure(figsize=(max(8, len(labels) * 1.6), 5))
    ax = fig.add_subplot(111)

    group_spacing = 3.0
    widths = 0.8
    hit_pos = [i * group_spacing for i in range(len(labels))]
    miss_pos = [i * group_spacing + 1.0 for i in range(len(labels))]

    bp_hit = ax.boxplot(hit_series, positions=hit_pos, widths=widths,
                        showfliers=False, patch_artist=True)
    bp_miss = ax.boxplot(miss_series, positions=miss_pos, widths=widths,
                         showfliers=False, patch_artist=True)

    # --- hatching for clarity (no colors) ---
    for b in bp_hit["boxes"]:
        b.set_facecolor("red")
        b.set_linewidth(1.3)
    for b in bp_miss["boxes"]:
        b.set_facecolor("blue")
        b.set_linewidth(1.3)
    for med in bp_hit["medians"]:
        med.set_linestyle("-")
        med.set_linewidth(1.6)
    for med in bp_miss["medians"]:
        med.set_linestyle("--")
        med.set_linewidth(1.6)

    # --- x-axis labels ---
    centers = [i * group_spacing + 0.5 for i in range(len(labels))]
    ax.set_xticks(centers)
    ax.set_xticklabels(labels, rotation=0)
    ax.set_title("Latency by cache segment across datasets")
    ax.set_ylabel("Latency (ms)")

    legend_patches = [
        Patch(facecolor="blue", hatch="", label="hit"),
        Patch(facecolor="red", hatch="", label="miss"),
    ]
    ax.legend(handles=legend_patches, loc="best", frameon=True)

    fig.tight_layout()
    fig.savefig(outdir / "latency_box_cache_all.png", dpi=150)
    plt.close(fig)


def plot_latency_cdf_by_cache(all_df: pd.DataFrame, algo: str, clients: int, outdir: Path):
    sub = all_df[(all_df["algo"]==algo) & (all_df["clients"]==clients)]
    if sub.empty:
        return
    fig = plt.figure(figsize=(7,5))
    ax = fig.add_subplot(111)
    for seg_bool, label in [(True,"hit"), (False,"miss")]:
        lat = np.sort(sub.loc[sub["from_cache"]==seg_bool, "latency_ms"].dropna().values)
        if lat.size == 0:
            continue
        y = np.arange(1, lat.size+1) / lat.size
        ax.plot(lat, y, label=label)
    ax.set_title(f"Latency CDF by cache segment - {algo}, {clients} clients")
    ax.set_xlabel("Latency (ms)")
    ax.set_ylabel("Cumulative probability")
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / f"latency_cdf_cache_{algo}_{clients}C.png", dpi=150)
    plt.close(fig)

def plot_throughput_vs_clients_by_segment(per_segment_df: pd.DataFrame, outdir: Path):
    if per_segment_df.empty:
        return
    fig = plt.figure(figsize=(8,5))
    ax = fig.add_subplot(111)
    for (algo, segment), g in per_segment_df.groupby(["algo","segment"]):
        g = g.sort_values("clients")
        ax.plot(g["clients"], g["throughput_req_s"], marker="o", label=f"{algo}-{segment}")
    ax.set_title("Throughput vs number of clients (by cache segment)")
    ax.set_xlabel("Clients")
    ax.set_ylabel("Throughput (req/s)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / "throughput_vs_clients_by_cache_segment.png", dpi=150)
    plt.close(fig)

def plot_cache_hit_ratio_vs_clients(summary_df: pd.DataFrame, outdir: Path):
    if summary_df.empty:
        return
    fig = plt.figure(figsize=(7,5))
    ax = fig.add_subplot(111)
    for algo, g in summary_df.groupby("algo"):
        g = g.sort_values("clients")
        ax.plot(g["clients"], g["cache_hit_ratio"], marker="o", label=algo)
    ax.set_title("Cache hit ratio vs number of clients")
    ax.set_xlabel("Clients")
    ax.set_ylabel("Cache hit ratio")
    ax.set_ylim(0, 1.0)
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / "cache_hit_ratio_vs_clients.png", dpi=150)
    plt.close(fig)

def main():
    parser = argparse.ArgumentParser(description="Analyze ADS results split by cache hit vs miss (LC vs RR across client scales).")
    parser.add_argument("--input", type=str, default="../results", help="Input folder containing result CSVs")
    parser.add_argument("--out", type=str, default="./figs", help="Output folder for figures and summary CSVs")
    args = parser.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    expected = [
        ("LC", 1,  in_dir / "LC_Results_1C_20T.csv"),
        ("LC", 10, in_dir / "LC_Results_10C_20T.csv"),
        ("LC", 100,in_dir / "LC_Results_100C_20T.csv"),
        ("RR", 1,  in_dir / "RR_Results_1C_20T.csv"),
        ("RR", 10, in_dir / "RR_Results_10C_20T.csv"),
        ("RR", 100,in_dir / "RR_Results_100C_20T.csv"),
    ]

    def find_fallback(algo, clients):
        candidates = sorted(in_dir.glob(f"{algo}_Results_{clients}C_*.csv"))
        return candidates[0] if candidates else None

    datasets = []
    for algo, clients, path in expected:
        csv_path = path if path.exists() else find_fallback(algo, clients)
        if not csv_path or not csv_path.exists():
            print(f"[WARN] Missing file for {algo} {clients}C at {path}. Skipping.", file=sys.stderr)
            continue
        try:
            df = load_dataset(csv_path, algo, clients)
            datasets.append(df)
        except Exception as e:
            print(f"[ERROR] Failed to load {csv_path}: {e}", file=sys.stderr)

    if not datasets:
        print("[ERROR] No datasets loaded. Check input folder and file names.", file=sys.stderr)
        sys.exit(1)

    all_df = pd.concat(datasets, ignore_index=True)
    all_df = all_df.sort_values(["algo","clients","t_start_utc"]).reset_index(drop=True)

    summaries = []
    per_segment_list = []
    server_dists_overall = {}
    server_dists_by_segment = {}
    per_server_lat_list = []

    for (algo, clients), sub in all_df.groupby(["algo","clients"], sort=True):
        summary, per_segment_df, server_counts, per_server_latency, server_dist_by_seg = compute_dataset_metrics(sub)
        summaries.append(summary)
        per_segment_list.append(per_segment_df)
        key = dataset_key(algo, clients)
        server_dists_overall[key] = server_counts
        server_dists_by_segment[key] = server_dist_by_seg
        per_server_lat_list.append(per_server_latency)

        # Plots for each dataset
        plot_server_distribution(server_counts, algo, int(clients), out_dir, suffix="all")
        # plot_latency_box_by_cache(all_df, algo, int(clients), out_dir)
        plot_combined_latency_box_by_cache(all_df, out_dir)
        plot_latency_cdf_by_cache(all_df, algo, int(clients), out_dir)

    summary_df = pd.DataFrame(summaries).sort_values(["algo","clients"]).reset_index(drop=True)
    per_segment_df = pd.concat(per_segment_list, ignore_index=True).sort_values(["algo","clients","segment"]).reset_index(drop=True)
    per_server_latency_df = pd.concat(per_server_lat_list, ignore_index=True)

    # Comparative plots
    plot_throughput_vs_clients_by_segment(per_segment_df, out_dir)
    # plot_cache_hit_ratio_vs_clients(summary_df, out_dir)

    # Save CSVs
    summary_df.to_csv(out_dir / "summary_overall.csv", index=False)
    per_segment_df.to_csv(out_dir / "summary_by_cache_segment.csv", index=False)
    per_server_latency_df.to_csv(out_dir / "summary_per_server_latency_by_segment.csv", index=False)

    # Dump server distributions
    with open(out_dir / "server_distributions_overall.csv","w") as f:
        f.write("algo,clients,server,requests\\n")
        for key, counts in server_dists_overall.items():
            algo, clients_str = key.split("-")[0], key.split("-")[1][:-1]
            for server, cnt in counts.items():
                f.write(f"{algo},{clients_str},{server},{cnt}\\n")
    with open(out_dir / "server_distributions_by_cache_segment.csv","w") as f:
        f.write("algo,clients,segment,server,requests\\n")
        for key, segmap in server_dists_by_segment.items():
            algo, clients_str = key.split("-")[0], key.split("-")[1][:-1]
            for seg, counts in segmap.items():
                for server, cnt in counts.items():
                    f.write(f"{algo},{clients_str},{seg},{server},{cnt}\\n")

    # Print concise tables
    def print_table(df: pd.DataFrame, title: str):
        print("\\n" + "="*len(title))
        print(title)
        print("="*len(title))
        if df.empty:
            print("(no data)")
            return
        with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 160):
            print(df.to_string(index=False))

    print_table(summary_df.round({
        "duration_s": 3, "throughput_req_s": 3,
        "lat_mean_ms": 3, "lat_median_ms": 3,
        "lat_p95_ms": 3, "lat_p99_ms": 3,
        "lat_min_ms": 3, "lat_max_ms": 3, "lat_std_ms": 3,
        "cache_hit_ratio": 3, "cache_hit_mean_lat_ms": 3, "cache_miss_mean_lat_ms": 3,
        "server_fairness_index": 3, "server_gini": 3,
    }), "Overall metrics per dataset (algo, clients)")

    print_table(per_segment_df.round({
        "duration_s": 3, "throughput_req_s": 3,
        "lat_mean_ms": 3, "lat_p50_ms": 3, "lat_p95_ms": 3, "lat_p99_ms": 3,
    }), "Metrics by cache segment (hit vs miss)")

    for (algo, clients), g in per_server_latency_df.groupby(["algo","clients"]):
        title = f"Per-server latency stats by segment - {algo}, {clients} clients"
        print_table(g.round({"lat_mean_ms":3,"lat_p95_ms":3}), title)

if __name__ == "__main__":
    main()
