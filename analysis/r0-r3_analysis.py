import zipfile
import os
import pandas as pd
import matplotlib.pyplot as plt

zip_path = "ROUND_1.zip"

all_prices = []
all_trades = []

with zipfile.ZipFile(zip_path) as z:
    for f in z.namelist():
        base = os.path.basename(f)

        if "__MACOSX" in f or not base.endswith(".csv"):
            continue

        if base.startswith("prices_"):
            temp = pd.read_csv(z.open(f), sep=";")
            temp["source_file"] = base
            all_prices.append(temp)

        elif base.startswith("trades_"):
            temp = pd.read_csv(z.open(f), sep=";")
            temp["source_file"] = base
            all_trades.append(temp)

prices = pd.concat(all_prices, ignore_index=True)
trades = pd.concat(all_trades, ignore_index=True)

print("Prices columns:", prices.columns.tolist())
print("Trades columns:", trades.columns.tolist())
print("Products in prices:", prices["product"].unique())
print("Products in trades:", trades["symbol"].unique())


# Make sure relevant columns are numeric
price_numeric_cols = [
    "timestamp",
    "bid_price_1", "bid_volume_1",
    "ask_price_1", "ask_volume_1",
    "mid_price"
]

for col in price_numeric_cols:
    if col in prices.columns:
        prices[col] = pd.to_numeric(prices[col], errors="coerce")

trade_numeric_cols = ["timestamp", "price", "quantity"]
for col in trade_numeric_cols:
    if col in trades.columns:
        trades[col] = pd.to_numeric(trades[col], errors="coerce")


# Clean invalid rows
invalid_mask = (
    prices["mid_price"].isna() |
    prices["bid_price_1"].isna() |
    prices["ask_price_1"].isna() |
    (prices["mid_price"] <= 0) |
    (prices["bid_price_1"] <= 0) |
    (prices["ask_price_1"] <= 0)
)

removed_rows = invalid_mask.sum()
print(f"\nRemoved {removed_rows} invalid price rows out of {len(prices)} total rows.")

prices_clean = prices.loc[~invalid_mask].copy()

prices_clean["spread"] = prices_clean["ask_price_1"] - prices_clean["bid_price_1"]

top_vol_sum = prices_clean["bid_volume_1"] + prices_clean["ask_volume_1"]
prices_clean["imbalance"] = (
    (prices_clean["bid_volume_1"] - prices_clean["ask_volume_1"]) /
    top_vol_sum.replace(0, pd.NA)
)

products = prices_clean["product"].dropna().unique()


# Summary stats by product
for product in products:
    df = prices_clean[prices_clean["product"] == product].copy()

    print(f"\n{'=' * 50}")
    print(product)
    print("Mid price summary")
    print(df["mid_price"].describe())

    print("\nSpread summary")
    print(df["spread"].describe())

    print("\nMost common mid prices")
    print(df["mid_price"].value_counts().head(10))


# Summary stats by product and source file
for product in products:
    df = prices_clean[prices_clean["product"] == product].copy()

    print(f"\n{'=' * 50}")
    print(f"{product} by source file")

    for file_name, group in df.groupby("source_file"):
        print(f"\nFile: {file_name}")
        print("Mid price summary")
        print(group["mid_price"].describe())
        print("Spread summary")
        print(group["spread"].describe())


# Plot cleaned mid price by day/file
for product in products:
    df = prices_clean[prices_clean["product"] == product].copy()

    plt.figure(figsize=(10, 4))
    for file_name, group in df.groupby("source_file"):
        group = group.sort_values("timestamp")
        plt.plot(group["timestamp"], group["mid_price"], label=file_name)

    plt.title(f"{product} Clean Mid Price by Day")
    plt.xlabel("Timestamp")
    plt.ylabel("Mid Price")
    plt.legend()
    plt.tight_layout()
    plt.show()



# Plot cleaned spread by day/file
for product in products:
    df = prices_clean[prices_clean["product"] == product].copy()

    plt.figure(figsize=(10, 4))
    for file_name, group in df.groupby("source_file"):
        group = group.sort_values("timestamp")
        plt.plot(group["timestamp"], group["spread"], label=file_name)

    plt.title(f"{product} Clean Spread by Day")
    plt.xlabel("Timestamp")
    plt.ylabel("Spread")
    plt.legend()
    plt.tight_layout()
    plt.show()


# Trade summaries
trade_products = trades["symbol"].dropna().unique()

for product in trade_products:
    t = trades[trades["symbol"] == product].copy()

    print(f"\n{'=' * 50}")
    print(f"{product} trades")
    print(t["price"].describe())
    print("Trade count:", len(t))
    print("Quantity summary:")
    print(t["quantity"].describe())

    print(f"\n{product} trades by source file")
    for file_name, group in t.groupby("source_file"):
        print(f"\nFile: {file_name}")
        print("Trade count:", len(group))
        print("Price summary:")
        print(group["price"].describe())
        print("Quantity summary:")
        print(group["quantity"].describe())
