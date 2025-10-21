import pandas as pd
import glob
import os

def combine_latency_csvs(folder="results", output_name="combined-latency.csv"):
    pattern = os.path.join(folder, "latency-*.csv")
    csv_files = glob.glob(pattern)

    if not csv_files:
        print("No CSV files found matching pattern:", pattern)
        return

    # Read and concatenate
    dfs = []
    for file in csv_files:
        try:
            df = pd.read_csv(file)
            df["source_file"] = os.path.basename(file)  # optional: track source
            dfs.append(df)
        except Exception as e:
            print(f"Skipping {file} due to error: {e}")

    if not dfs:
        print("No valid CSV files to combine.")
        return

    combined = pd.concat(dfs, ignore_index=True)

    output_path = os.path.join(folder, output_name)
    combined.to_csv(output_path, index=False)
    print(f"Combined CSV saved to: {output_path}")

if __name__ == "__main__":
    combine_latency_csvs()
