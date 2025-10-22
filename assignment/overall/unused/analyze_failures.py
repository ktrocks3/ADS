import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os

def analyze_failure_csv(path):
    # Load CSV
    df = pd.read_csv(path)
    print(f"Loaded {len(df)} rows from {path}")

    # Parse timestamp and compute relative time (seconds)
    df["t_start_utc"] = pd.to_datetime(df["t_start_utc"], utc=True, errors="coerce")
    df = df.dropna(subset=["t_start_utc"])
    t0 = df["t_start_utc"].min()
    df["t_rel_s"] = (df["t_start_utc"] - t0).dt.total_seconds()

    # Clean up
    df["latency_ms"] = pd.to_numeric(df["latency_ms"], errors="coerce")
    df = df.dropna(subset=["latency_ms"])
    df["server"] = df["server"].astype(str)

    # Identify failed requests
    df["failed"] = (df["server"] == "-") | (df["count"] == -1)

    # Compute rolling median to show trend
    df = df.sort_values("t_rel_s")
    roll = df["latency_ms"].rolling(window=10, min_periods=1, center=True).median()

    # --- Plot ---
    fig, ax = plt.subplots(figsize=(12, 5))
    for srv, sub in df[~df["failed"]].groupby("server"):
        ax.scatter(sub["t_rel_s"], sub["latency_ms"], s=15, label=srv[:6], alpha=0.7)

    # failed requests in red
    fails = df[df["failed"]]
    if not fails.empty:
        ax.scatter(fails["t_rel_s"], fails["latency_ms"], s=25, c="red", marker="x", label="Failed")

    # rolling median line
    ax.plot(df["t_rel_s"], roll, color="black", linewidth=1.5, label="Rolling median")

    ax.set_title("Request Latency over Time with Server Failures")
    ax.set_xlabel("Time since start (s)")
    ax.set_ylabel("Latency (ms)")
    ax.legend(loc="upper left", frameon=False, ncol=4)
    ax.grid(True, alpha=0.3)

    # autoscale y but clip crazy outliers > max(99.9%)
    ymax = np.percentile(df["latency_ms"], 99.9)
    ax.set_ylim(0, ymax * 1.1)

    outfile = os.path.splitext(path)[0] + "_failure_plot.png"
    plt.tight_layout()
    plt.savefig(outfile, dpi=180)
    print(f"Saved {outfile}")
    plt.show()

if __name__ == "__main__":
    # Edit this to your CSV filename if different
    analyze_failure_csv("../../phase3/results/FailureDetection.csv")
