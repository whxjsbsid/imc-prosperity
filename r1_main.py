import json
import math
from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Any


class Trader:
    LIMITS = {
        "ASH_COATED_OSMIUM": 80,
        "INTARIAN_PEPPER_ROOT": 80,
    }

    DAY_END = 1_000_000

    def run(self, state: TradingState):
        result = {}
        conversions = 0

        # Restore saved state
        if state.traderData:
            try:
                data: Dict[str, Any] = json.loads(state.traderData)
            except Exception:
                data = {}
        else:
            data = {}

        for product, order_depth in state.order_depths.items():
            orders: List[Order] = []

            if product not in self.LIMITS:
                continue

            if len(order_depth.buy_orders) == 0 or len(order_depth.sell_orders) == 0:
                result[product] = orders
                continue

            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())
            mid_price = (best_bid + best_ask) / 2

            position = state.position.get(product, 0)
            limit = self.LIMITS[product]

            # -------------------------
            # Fair value
            # -------------------------
            if product == "ASH_COATED_OSMIUM":
                fair_value = 10000.0
                take_edge = 2
                make_edge = 1
                passive_size = 12

            elif product == "INTARIAN_PEPPER_ROOT":
                # Save first observed mid price and timestamp
                if "root_first_mid" not in data:
                    data["root_first_mid"] = mid_price
                    data["root_first_ts"] = state.timestamp

                first_mid = float(data["root_first_mid"])
                first_ts = int(data["root_first_ts"])

                if self.DAY_END <= first_ts:
                    progress = 1.0
                else:
                    progress = (state.timestamp - first_ts) / (self.DAY_END - first_ts)
                    progress = max(0.0, min(1.0, progress))

                fair_value = first_mid + 1000.0 * progress
                take_edge = 3
                make_edge = 1
                passive_size = 12

            # -------------------------
            # Position capacity
            # -------------------------
            max_buy = limit - position
            max_sell = limit + position

            # -------------------------
            # Mean-reversion market taking
            # Buy when market is below fair value
            # Sell when market is above fair value
            # -------------------------
            for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                if max_buy <= 0:
                    break
                if ask_price <= fair_value - take_edge:
                    trade_qty = min(max_buy, -ask_volume)
                    if trade_qty > 0:
                        orders.append(Order(product, ask_price, trade_qty))
                        max_buy -= trade_qty
                else:
                    break

            for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
                if max_sell <= 0:
                    break
                if bid_price >= fair_value + take_edge:
                    trade_qty = min(max_sell, bid_volume)
                    if trade_qty > 0:
                        orders.append(Order(product, bid_price, -trade_qty))
                        max_sell -= trade_qty
                else:
                    break

            # -------------------------
            # Passive mean-reversion quoting
            # Quote around fair value, with mild inventory leaning
            # -------------------------
            inventory_adjust = position / 20.0

            buy_quote = math.floor(fair_value - make_edge - inventory_adjust)
            sell_quote = math.ceil(fair_value + make_edge - inventory_adjust)

            # Do not cross the market when posting passive quotes
            buy_quote = min(buy_quote, best_bid + 1)
            sell_quote = max(sell_quote, best_ask - 1)

            # Size leaning: if long, quote more on sell side; if short, more on buy side
            buy_size = min(max_buy, passive_size)
            sell_size = min(max_sell, passive_size)

            if position > 0:
                buy_size = min(max_buy, max(0, passive_size - position // 10))
                sell_size = min(max_sell, passive_size + position // 10)
            elif position < 0:
                buy_size = min(max_buy, passive_size + (-position) // 10)
                sell_size = min(max_sell, max(0, passive_size - (-position) // 10))

            if buy_size > 0:
                orders.append(Order(product, buy_quote, buy_size))

            if sell_size > 0:
                orders.append(Order(product, sell_quote, -sell_size))

            result[product] = orders

        traderData = json.dumps(data)
        return result, conversions, traderData
