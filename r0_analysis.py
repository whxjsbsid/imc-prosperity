import zipfile
import pandas as pd
import matplotlib.pyplot as plt

zip_path = "TUTORIAL_ROUND_1.zip"

# Load all csvs from the zip
with zipfile.ZipFile(zip_path) as z:
    price_files = [f for f in z.namelist() if f.startswith("prices_") and f.endswith(".csv")]
    trade_files = [f for f in z.namelist() if f.startswith("trades_") and f.endswith(".csv")]

    prices = pd.concat(
        [pd.read_csv(z.open(f), sep=";") for f in price_files],
        ignore_index=True
    )
    trades = pd.concat(
        [pd.read_csv(z.open(f), sep=";") for f in trade_files],
        ignore_index=True
    )

print("Prices columns:", prices.columns.tolist())
print("Trades columns:", trades.columns.tolist())
print("Products in prices:", prices["product"].unique())
print("Products in trades:", trades["symbol"].unique())

# Split by product
emeralds = prices[prices["product"] == "EMERALDS"].copy()
tomatoes = prices[prices["product"] == "TOMATOES"].copy()

# Basic derived columns
for df in [emeralds, tomatoes]:
    df["spread"] = df["ask_price_1"] - df["bid_price_1"]
    df["imbalance"] = (df["bid_volume_1"] - df["ask_volume_1"]) / (
        df["bid_volume_1"] + df["ask_volume_1"]
    )

# Summary stats
for name, df in [("EMERALDS", emeralds), ("TOMATOES", tomatoes)]:
    print(f"\n{name}")
    print("Mid price summary")
    print(df["mid_price"].describe())
    print("\nSpread summary")
    print(df["spread"].describe())
    print("\nMost common mid prices")
    print(df["mid_price"].value_counts().head(10))

# Plot mid price
for name, df in [("EMERALDS", emeralds), ("TOMATOES", tomatoes)]:
    plt.figure(figsize=(10, 4))
    plt.plot(df["timestamp"], df["mid_price"])
    plt.title(f"{name} Mid Price")
    plt.xlabel("Timestamp")
    plt.ylabel("Mid Price")
    plt.show()

# Plot spread
for name, df in [("EMERALDS", emeralds), ("TOMATOES", tomatoes)]:
    plt.figure(figsize=(10, 4))
    plt.plot(df["timestamp"], df["spread"])
    plt.title(f"{name} Spread")
    plt.xlabel("Timestamp")
    plt.ylabel("Spread")
    plt.show()

# Trade summaries
for name in ["EMERALDS", "TOMATOES"]:
    t = trades[trades["symbol"] == name].copy()
    print(f"\n{name} trades")
    print(t["price"].describe())
    print("Trade count:", len(t))
    print("Quantity summary:")
    print(t["quantity"].describe())
