import json
import math
from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Optional, Tuple, Any


class Trader:
    LIMITS = {
        "ASH_COATED_OSMIUM": 80,
        "INTARIAN_PEPPER_ROOT": 80,
    }

    OSMIUM_FAIR_VALUE = 10000
    ROOT_TOTAL_DRIFT = 1000.0
    DAY_END = 999900

    OSMIUM_PASSIVE_SIZE = 20
    ROOT_PASSIVE_SIZE = 12
    OSMIUM_FLATTEN_THRESHOLD = 50
    ROOT_SKEW_THRESHOLD = 60

    def best_bid_ask(self, order_depth: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
        best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
        return best_bid, best_ask

    def get_mid_price(self, order_depth: OrderDepth) -> Optional[float]:
        best_bid, best_ask = self.best_bid_ask(order_depth)
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2.0

    def take_cheap_asks(
        self,
        product: str,
        order_depth: OrderDepth,
        orders: List[Order],
        fair_value: float,
        max_buy: int,
    ) -> int:
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if max_buy <= 0:
                break
            if ask_price < fair_value:
                trade_qty = min(max_buy, -ask_volume)
                if trade_qty > 0:
                    orders.append(Order(product, ask_price, trade_qty))
                    max_buy -= trade_qty
            else:
                break
        return max_buy

    def take_rich_bids(
        self,
        product: str,
        order_depth: OrderDepth,
        orders: List[Order],
        fair_value: float,
        max_sell: int,
    ) -> int:
        for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if max_sell <= 0:
                break
            if bid_price > fair_value:
                trade_qty = min(max_sell, bid_volume)
                if trade_qty > 0:
                    orders.append(Order(product, bid_price, -trade_qty))
                    max_sell -= trade_qty
            else:
                break
        return max_sell

    def trade_osmium(self, state: TradingState, order_depth: OrderDepth) -> List[Order]:
        product = "ASH_COATED_OSMIUM"
        fair_value = float(self.OSMIUM_FAIR_VALUE)
        position = state.position.get(product, 0)
        limit = self.LIMITS[product]
        orders: List[Order] = []

        best_bid, best_ask = self.best_bid_ask(order_depth)
        max_buy = limit - position
        max_sell = limit + position

        # 1) Immediately take any favorable trades.
        max_buy = self.take_cheap_asks(product, order_depth, orders, fair_value, max_buy)
        max_sell = self.take_rich_bids(product, order_depth, orders, fair_value, max_sell)

        # 2) If inventory is too skewed, flatten at exactly 10,000.
        if position > self.OSMIUM_FLATTEN_THRESHOLD and max_sell > 0:
            flatten_qty = min(position, max_sell)
            if flatten_qty > 0:
                orders.append(Order(product, self.OSMIUM_FAIR_VALUE, -flatten_qty))
                max_sell -= flatten_qty
        elif position < -self.OSMIUM_FLATTEN_THRESHOLD and max_buy > 0:
            flatten_qty = min(-position, max_buy)
            if flatten_qty > 0:
                orders.append(Order(product, self.OSMIUM_FAIR_VALUE, flatten_qty))
                max_buy -= flatten_qty

        # 3) Passive quoting: overbid bids / undercut asks while preserving positive edge.
        if best_bid is not None and best_ask is not None:
            buy_quote = best_bid + 1
            sell_quote = best_ask - 1

            # Only quote where the edge versus fair value remains positive.
            if buy_quote < self.OSMIUM_FAIR_VALUE and buy_quote < best_ask and max_buy > 0 and position < self.OSMIUM_FLATTEN_THRESHOLD:
                buy_size = min(max_buy, self.OSMIUM_PASSIVE_SIZE)
                if buy_size > 0:
                    orders.append(Order(product, buy_quote, buy_size))

            if sell_quote > self.OSMIUM_FAIR_VALUE and sell_quote > best_bid and max_sell > 0 and position > -self.OSMIUM_FLATTEN_THRESHOLD:
                sell_size = min(max_sell, self.OSMIUM_PASSIVE_SIZE)
                if sell_size > 0:
                    orders.append(Order(product, sell_quote, -sell_size))

        return orders

    def trade_root(self, state: TradingState, order_depth: OrderDepth, data: Dict[str, Any]) -> List[Order]:
        product = "INTARIAN_PEPPER_ROOT"
        position = state.position.get(product, 0)
        limit = self.LIMITS[product]
        orders: List[Order] = []

        mid_price = self.get_mid_price(order_depth)
        best_bid, best_ask = self.best_bid_ask(order_depth)

        # Initialize the day anchor using the first observed mid-price.
        if state.timestamp == 0 or "root_first_mid" not in data:
            if mid_price is not None:
                data["root_first_mid"] = mid_price
            else:
                # Fallback if one side of the book is missing on the first tick.
                if best_bid is not None and best_ask is None:
                    data["root_first_mid"] = float(best_bid)
                elif best_ask is not None and best_bid is None:
                    data["root_first_mid"] = float(best_ask)
                else:
                    data["root_first_mid"] = 0.0

        progress = max(0.0, min(1.0, state.timestamp / self.DAY_END)) if self.DAY_END > 0 else 1.0
        fair_value = float(data["root_first_mid"]) + self.ROOT_TOTAL_DRIFT * progress

        max_buy = limit - position
        max_sell = limit + position

        # Mean reversion: buy below the moving fair value, sell above it.
        max_buy = self.take_cheap_asks(product, order_depth, orders, fair_value, max_buy)
        max_sell = self.take_rich_bids(product, order_depth, orders, fair_value, max_sell)

        if best_bid is None or best_ask is None:
            return orders

        # Passive quotes slightly better than existing liquidity while preserving edge.
        buy_quote = min(best_bid + 1, math.floor(fair_value - 1))
        sell_quote = max(best_ask - 1, math.ceil(fair_value + 1))

        # Mild inventory management: reduce quoting on the side that worsens inventory.
        buy_size = min(max_buy, self.ROOT_PASSIVE_SIZE)
        sell_size = min(max_sell, self.ROOT_PASSIVE_SIZE)

        if position > 20:
            buy_size = max(0, buy_size - position // 10)
        if position < -20:
            sell_size = max(0, sell_size - (-position) // 10)

        if position >= self.ROOT_SKEW_THRESHOLD:
            buy_size = 0
            if max_sell > 0:
                rebalance_price = max(math.ceil(fair_value), sell_quote)
                rebalance_qty = min(max_sell, position)
                if rebalance_qty > 0:
                    orders.append(Order(product, rebalance_price, -rebalance_qty))
        elif position <= -self.ROOT_SKEW_THRESHOLD:
            sell_size = 0
            if max_buy > 0:
                rebalance_price = min(math.floor(fair_value), buy_quote)
                rebalance_qty = min(max_buy, -position)
                if rebalance_qty > 0:
                    orders.append(Order(product, rebalance_price, rebalance_qty))

        if buy_size > 0 and buy_quote < fair_value and buy_quote < best_ask:
            orders.append(Order(product, buy_quote, buy_size))

        if sell_size > 0 and sell_quote > fair_value and sell_quote > best_bid:
            orders.append(Order(product, sell_quote, -sell_size))

        return orders

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        if state.traderData:
            try:
                data: Dict[str, Any] = json.loads(state.traderData)
            except Exception:
                data = {}
        else:
            data = {}

        for product, order_depth in state.order_depths.items():
            if product == "ASH_COATED_OSMIUM":
                result[product] = self.trade_osmium(state, order_depth)
            elif product == "INTARIAN_PEPPER_ROOT":
                result[product] = self.trade_root(state, order_depth, data)
            else:
                result[product] = []

        traderData = json.dumps(data)
        return result, conversions, traderData
