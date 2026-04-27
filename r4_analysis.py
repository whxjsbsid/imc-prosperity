import argparse
import os
import re
import zipfile

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# Change this if your zip has a different name.
DEFAULT_ZIP_PATH = "ROUND_4.zip"

# Use short and medium horizons.
# In Prosperity data, timestamps usually move in 100-step increments.
DEFAULT_HORIZONS = [100, 500, 1000, 2000, 5000]

# A trader needs enough observations before we treat the result as meaningful.
MIN_TRADES_FOR_FLAG = 50


# ============================================================
# Loading
# ============================================================

def extract_day_from_file(file_name):
    """Extract day number from names like prices_round_4_day_1.csv."""
    match = re.search(r"day_(-?\d+)", file_name)
    if match:
        return int(match.group(1))
    return np.nan


def load_round_zip(zip_path):
    """Load all prices_*.csv and trades_*.csv files from a Prosperity round zip."""
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
                if "day" not in temp.columns:
                    temp["day"] = extract_day_from_file(base)
                all_prices.append(temp)

            elif base.startswith("trades_"):
                temp = pd.read_csv(z.open(f), sep=";")
                temp["source_file"] = base
                temp["day"] = extract_day_from_file(base)
                all_trades.append(temp)

    if not all_prices:
        raise ValueError("No prices_*.csv files found in the zip.")
    if not all_trades:
        raise ValueError("No trades_*.csv files found in the zip.")

    prices = pd.concat(all_prices, ignore_index=True)
    trades = pd.concat(all_trades, ignore_index=True)

    return prices, trades


def clean_prices_and_trades(prices, trades):
    """Convert key columns to numeric and remove unusable price rows."""
    price_numeric_cols = [
        "day",
        "timestamp",
        "bid_price_1", "bid_volume_1",
        "ask_price_1", "ask_volume_1",
        "mid_price",
    ]

    for col in price_numeric_cols:
        if col in prices.columns:
            prices[col] = pd.to_numeric(prices[col], errors="coerce")

    trade_numeric_cols = ["day", "timestamp", "price", "quantity"]
    for col in trade_numeric_cols:
        if col in trades.columns:
            trades[col] = pd.to_numeric(trades[col], errors="coerce")

    prices["product"] = prices["product"].astype(str)
    trades["symbol"] = trades["symbol"].astype(str)

    # For VEV options, bid_price_1 can be 0 when the option is almost worthless.
    # So do NOT filter out bid_price_1 == 0. Only require a valid positive mid.
    invalid_price_mask = (
        prices["day"].isna()
        | prices["timestamp"].isna()
        | prices["product"].isna()
        | prices["mid_price"].isna()
        | (prices["mid_price"] <= 0)
    )

    prices_clean = prices.loc[~invalid_price_mask].copy()

    if "bid_price_1" in prices_clean.columns and "ask_price_1" in prices_clean.columns:
        prices_clean["spread"] = prices_clean["ask_price_1"] - prices_clean["bid_price_1"]

    if "bid_volume_1" in prices_clean.columns and "ask_volume_1" in prices_clean.columns:
        top_vol_sum = prices_clean["bid_volume_1"] + prices_clean["ask_volume_1"]
        prices_clean["imbalance"] = (
            (prices_clean["bid_volume_1"] - prices_clean["ask_volume_1"])
            / top_vol_sum.replace(0, pd.NA)
        )

    invalid_trade_mask = (
        trades["day"].isna()
        | trades["timestamp"].isna()
        | trades["symbol"].isna()
        | trades["price"].isna()
        | trades["quantity"].isna()
        | (trades["quantity"] <= 0)
    )

    trades_clean = trades.loc[~invalid_trade_mask].copy()

    return prices_clean, trades_clean


# ============================================================
# Price alignment
# ============================================================

def attach_current_and_future_mids(trades, prices, horizons):
    """
    For each trade, attach:
    - mid_at_trade: latest available mid at or before trade timestamp
    - mid_fwd_H: first mid at or after timestamp + H
    """
    price_small = (
        prices[["day", "timestamp", "product", "mid_price"]]
        .rename(columns={"product": "symbol"})
        .sort_values(["day", "symbol", "timestamp"])
    )

    out = []

    for (day, symbol), trade_group in trades.groupby(["day", "symbol"], sort=False):
        price_group = price_small[
            (price_small["day"] == day) & (price_small["symbol"] == symbol)
        ].sort_values("timestamp")

        temp = trade_group.sort_values("timestamp").copy()

        if price_group.empty:
            temp["mid_at_trade"] = np.nan
            for h in horizons:
                temp[f"mid_fwd_{h}"] = np.nan
            out.append(temp)
            continue

        temp = pd.merge_asof(
            temp,
            price_group[["timestamp", "mid_price"]].rename(
                columns={"mid_price": "mid_at_trade"}
            ),
            on="timestamp",
            direction="backward",
        )

        for h in horizons:
            left = pd.DataFrame({"future_ts": temp["timestamp"] + h})
            right = price_group[["timestamp", "mid_price"]].rename(
                columns={"timestamp": "future_ts", "mid_price": f"mid_fwd_{h}"}
            )

            future = pd.merge_asof(
                left.sort_values("future_ts"),
                right.sort_values("future_ts"),
                on="future_ts",
                direction="forward",
            )

            temp[f"mid_fwd_{h}"] = future[f"mid_fwd_{h}"].to_numpy()

        out.append(temp)

    enriched = pd.concat(out, ignore_index=True)
    return enriched


def explode_trades_to_trader_actions(enriched_trades):
    """
    Convert each trade into two trader-action rows:
    - buyer gets +quantity
    - seller gets -quantity

    This lets us score each trader's side independently.
    """
    buyer_actions = enriched_trades.copy()
    buyer_actions["trader"] = buyer_actions["buyer"]
    buyer_actions["side"] = "BUY"
    buyer_actions["signed_qty"] = buyer_actions["quantity"]

    seller_actions = enriched_trades.copy()
    seller_actions["trader"] = seller_actions["seller"]
    seller_actions["side"] = "SELL"
    seller_actions["signed_qty"] = -seller_actions["quantity"]

    actions = pd.concat([buyer_actions, seller_actions], ignore_index=True)
    actions = actions.dropna(subset=["trader"]).copy()

    actions["abs_qty"] = actions["quantity"].abs()
    actions["side_sign"] = np.sign(actions["signed_qty"])

    return actions


# ============================================================
# Scoring
# ============================================================

def score_actions(actions, horizons):
    """
    Main idea:

    1) execution_edge:
       BUY is good if future_mid > trade_price.
       SELL is good if future_mid < trade_price.
       This captures profitable fills, but it can over-reward market makers.

    2) directional_edge:
       BUY is good if future_mid > mid_at_trade.
       SELL is good if future_mid < mid_at_trade.
       This is more useful for finding a potential informed / insider-style trader,
       because it ignores the bid-ask execution advantage and asks:
       "Did the trader choose the right direction before the mid moved?"
    """
    scored = actions.copy()

    for h in horizons:
        fwd_col = f"mid_fwd_{h}"

        scored[f"execution_unit_edge_{h}"] = (
            scored["side_sign"] * (scored[fwd_col] - scored["price"])
        )
        scored[f"execution_edge_{h}"] = (
            scored["signed_qty"] * (scored[fwd_col] - scored["price"])
        )
        scored[f"execution_correct_{h}"] = scored[f"execution_unit_edge_{h}"] > 0

        scored[f"directional_unit_edge_{h}"] = (
            scored["side_sign"] * (scored[fwd_col] - scored["mid_at_trade"])
        )
        scored[f"directional_edge_{h}"] = (
            scored["signed_qty"] * (scored[fwd_col] - scored["mid_at_trade"])
        )
        scored[f"directional_correct_{h}"] = scored[f"directional_unit_edge_{h}"] > 0

    return scored


def summarize_by_trader(scored, horizons):
    summaries = []

    for h in horizons:
        needed = [f"execution_edge_{h}", f"directional_edge_{h}"]
        valid = scored.dropna(subset=needed).copy()

        summary = valid.groupby("trader").agg(
            trades=("trader", "size"),
            total_qty=("abs_qty", "sum"),

            total_execution_edge=(f"execution_edge_{h}", "sum"),
            execution_edge_per_qty=(f"execution_unit_edge_{h}", "mean"),
            execution_hit_rate=(f"execution_correct_{h}", "mean"),

            total_directional_edge=(f"directional_edge_{h}", "sum"),
            directional_edge_per_qty=(f"directional_unit_edge_{h}", "mean"),
            directional_hit_rate=(f"directional_correct_{h}", "mean"),
        )

        summary["horizon"] = h

        # Cross-sectional suspicion score.
        # Higher = more likely to be directionally informed.
        # We combine:
        # - directional edge per quantity
        # - directional hit rate
        # - log sample size, so a tiny lucky sample does not dominate too much
        for col in ["directional_edge_per_qty", "directional_hit_rate"]:
            std = summary[col].std(ddof=0)
            if std == 0 or pd.isna(std):
                summary[f"z_{col}"] = 0.0
            else:
                summary[f"z_{col}"] = (summary[col] - summary[col].mean()) / std

        log_trades = np.log1p(summary["trades"])
        log_std = log_trades.std(ddof=0)
        if log_std == 0 or pd.isna(log_std):
            summary["z_log_trades"] = 0.0
        else:
            summary["z_log_trades"] = (log_trades - log_trades.mean()) / log_std

        summary["insider_score"] = (
            summary["z_directional_edge_per_qty"]
            + summary["z_directional_hit_rate"]
            + 0.25 * summary["z_log_trades"]
        )

        summaries.append(summary.reset_index())

    return pd.concat(summaries, ignore_index=True)


def summarize_by_trader_product(scored, horizons):
    summaries = []

    for h in horizons:
        needed = [f"execution_edge_{h}", f"directional_edge_{h}"]
        valid = scored.dropna(subset=needed).copy()

        summary = valid.groupby(["trader", "symbol"]).agg(
            trades=("trader", "size"),
            total_qty=("abs_qty", "sum"),

            total_execution_edge=(f"execution_edge_{h}", "sum"),
            execution_edge_per_qty=(f"execution_unit_edge_{h}", "mean"),
            execution_hit_rate=(f"execution_correct_{h}", "mean"),

            total_directional_edge=(f"directional_edge_{h}", "sum"),
            directional_edge_per_qty=(f"directional_unit_edge_{h}", "mean"),
            directional_hit_rate=(f"directional_correct_{h}", "mean"),
        )

        summary["horizon"] = h
        summaries.append(summary.reset_index())

    return pd.concat(summaries, ignore_index=True)


def flag_potential_insiders(trader_summary, min_trades=MIN_TRADES_FOR_FLAG):
    """
    Produce a compact candidate table.

    We flag traders who rank well by directional score across several horizons.
    This intentionally does not rely only on total PnL-like edge.
    """
    eligible = trader_summary[trader_summary["trades"] >= min_trades].copy()

    if eligible.empty:
        return eligible

    eligible["rank_by_horizon"] = eligible.groupby("horizon")["insider_score"].rank(
        ascending=False,
        method="min",
    )

    final = eligible.groupby("trader").agg(
        horizons_seen=("horizon", "nunique"),
        avg_rank=("rank_by_horizon", "mean"),
        best_rank=("rank_by_horizon", "min"),
        avg_insider_score=("insider_score", "mean"),
        max_insider_score=("insider_score", "max"),
        avg_directional_hit_rate=("directional_hit_rate", "mean"),
        avg_directional_edge_per_qty=("directional_edge_per_qty", "mean"),
        avg_execution_edge_per_qty=("execution_edge_per_qty", "mean"),
        total_qty=("total_qty", "mean"),
        avg_trades=("trades", "mean"),
    )

    final = final.sort_values(
        ["avg_rank", "avg_insider_score", "avg_directional_hit_rate"],
        ascending=[True, False, False],
    )

    return final.reset_index()


# ============================================================
# Optional pattern tables
# ============================================================

def quantity_pattern_table(scored):
    """Shows whether a trader repeatedly uses a distinctive quantity."""
    return (
        scored.groupby(["trader", "symbol", "side", "quantity"])
        .size()
        .reset_index(name="count")
        .sort_values(["trader", "symbol", "count"], ascending=[True, True, False])
    )


def counterparty_table(scored, candidate_trader=None):
    """Shows who each trader is usually trading against."""
    temp = scored.copy()
    temp["counterparty"] = np.where(temp["side"] == "BUY", temp["seller"], temp["buyer"])

    if candidate_trader is not None:
        temp = temp[temp["trader"] == candidate_trader]

    return (
        temp.groupby(["trader", "counterparty"])
        .agg(trades=("trader", "size"), total_qty=("abs_qty", "sum"))
        .reset_index()
        .sort_values(["trader", "trades"], ascending=[True, False])
    )


# ============================================================
# Output and plotting
# ============================================================

def save_outputs(
    scored,
    trader_summary,
    trader_product_summary,
    flagged,
    output_dir,
    main_horizon,
):
    os.makedirs(output_dir, exist_ok=True)

    scored.to_csv(os.path.join(output_dir, "enriched_trader_actions.csv"), index=False)
    trader_summary.to_csv(os.path.join(output_dir, "trader_insider_scores.csv"), index=False)
    trader_product_summary.to_csv(
        os.path.join(output_dir, "trader_product_insider_scores.csv"),
        index=False,
    )
    flagged.to_csv(os.path.join(output_dir, "potential_insider_candidates.csv"), index=False)

    quantity_pattern_table(scored).to_csv(
        os.path.join(output_dir, "quantity_patterns.csv"),
        index=False,
    )

    counterparty_table(scored).to_csv(
        os.path.join(output_dir, "counterparty_patterns.csv"),
        index=False,
    )

    # Plot 1: directional edge per quantity by trader
    plot_data = trader_summary[trader_summary["horizon"] == main_horizon].copy()
    plot_data = plot_data.sort_values("directional_edge_per_qty", ascending=False)

    plt.figure(figsize=(10, 4))
    plt.bar(plot_data["trader"], plot_data["directional_edge_per_qty"])
    plt.title(f"Directional Edge per Trade Unit by Trader, horizon={main_horizon}")
    plt.xlabel("Trader")
    plt.ylabel("Avg signed future mid move")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"directional_edge_h{main_horizon}.png"), dpi=150)
    plt.close()

    # Plot 2: execution edge per quantity by trader
    plot_data = plot_data.sort_values("execution_edge_per_qty", ascending=False)

    plt.figure(figsize=(10, 4))
    plt.bar(plot_data["trader"], plot_data["execution_edge_per_qty"])
    plt.title(f"Execution Edge per Trade Unit by Trader, horizon={main_horizon}")
    plt.xlabel("Trader")
    plt.ylabel("Avg signed future mid minus trade price")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"execution_edge_h{main_horizon}.png"), dpi=150)
    plt.close()


def print_console_summary(prices, trades, trader_summary, trader_product_summary, flagged, horizons):
    print("\nLoaded data")
    print("-" * 60)
    print("Price rows:", len(prices))
    print("Trade rows:", len(trades))
    print("Products:", sorted(prices["product"].dropna().unique().tolist()))
    print("Traders:", sorted(set(trades["buyer"].dropna()).union(set(trades["seller"].dropna()))))

    print("\nPotential insider / informed-direction candidates")
    print("-" * 60)
    if flagged.empty:
        print("No candidates met the minimum trade threshold.")
    else:
        print(flagged.head(10).round(4).to_string(index=False))

    for h in horizons:
        print(f"\nTop traders by insider_score, horizon={h}")
        print("-" * 60)
        cols = [
            "trader",
            "trades",
            "total_qty",
            "insider_score",
            "directional_edge_per_qty",
            "directional_hit_rate",
            "execution_edge_per_qty",
            "execution_hit_rate",
        ]
        top = (
            trader_summary[trader_summary["horizon"] == h]
            .sort_values("insider_score", ascending=False)
            .head(10)
        )
        print(top[cols].round(4).to_string(index=False))

    main_h = horizons[min(2, len(horizons) - 1)]
    print(f"\nTop trader-product pairs by directional edge, horizon={main_h}")
    print("-" * 60)
    cols = [
        "trader",
        "symbol",
        "trades",
        "total_qty",
        "total_directional_edge",
        "directional_edge_per_qty",
        "directional_hit_rate",
        "total_execution_edge",
        "execution_edge_per_qty",
    ]
    top_prod = (
        trader_product_summary[trader_product_summary["horizon"] == main_h]
        .sort_values("total_directional_edge", ascending=False)
        .head(15)
    )
    print(top_prod[cols].round(4).to_string(index=False))

    print("\nHow to read the output")
    print("-" * 60)
    print("directional_* = buy before mid rises / sell before mid falls. Better for spotting an informed trader.")
    print("execution_*   = future mid vs actual trade price. Useful, but can over-reward good market makers.")
    print("A strong insider candidate should usually have positive directional edge across multiple horizons.")


def main():
    parser = argparse.ArgumentParser(
        description="Prosperity trade analysis: identify potential informed / insider-style traders."
    )
    parser.add_argument(
        "--zip",
        dest="zip_path",
        default=DEFAULT_ZIP_PATH,
        help="Path to ROUND_*.zip containing prices_*.csv and trades_*.csv files.",
    )
    parser.add_argument(
        "--output",
        dest="output_dir",
        default="insider_analysis_output",
        help="Folder for CSV and PNG outputs.",
    )
    parser.add_argument(
        "--horizons",
        nargs="+",
        type=int,
        default=DEFAULT_HORIZONS,
        help="Forward horizons to test, e.g. --horizons 100 500 1000 5000.",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=MIN_TRADES_FOR_FLAG,
        help="Minimum trader-action rows needed before flagging a candidate.",
    )
    args = parser.parse_args()

    zip_path = args.zip_path
    if not os.path.exists(zip_path):
        # Helpful when running inside a notebook / ChatGPT sandbox.
        alt_path = os.path.join("/mnt/data", os.path.basename(zip_path))
        if os.path.exists(alt_path):
            zip_path = alt_path

    prices, trades = load_round_zip(zip_path)
    prices_clean, trades_clean = clean_prices_and_trades(prices, trades)

    enriched_trades = attach_current_and_future_mids(
        trades_clean,
        prices_clean,
        horizons=args.horizons,
    )

    actions = explode_trades_to_trader_actions(enriched_trades)
    scored = score_actions(actions, horizons=args.horizons)

    trader_summary = summarize_by_trader(scored, horizons=args.horizons)
    trader_product_summary = summarize_by_trader_product(scored, horizons=args.horizons)
    flagged = flag_potential_insiders(trader_summary, min_trades=args.min_trades)

    main_horizon = args.horizons[min(2, len(args.horizons) - 1)]

    save_outputs(
        scored=scored,
        trader_summary=trader_summary,
        trader_product_summary=trader_product_summary,
        flagged=flagged,
        output_dir=args.output_dir,
        main_horizon=main_horizon,
    )

    print_console_summary(
        prices=prices_clean,
        trades=trades_clean,
        trader_summary=trader_summary,
        trader_product_summary=trader_product_summary,
        flagged=flagged,
        horizons=args.horizons,
    )

    print(f"\nSaved CSVs and charts to: {args.output_dir}")


if __name__ == "__main__":
    main()
