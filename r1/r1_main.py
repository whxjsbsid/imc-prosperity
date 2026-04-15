import json
from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Any


class Trader:
    LIMITS = {
        "ASH_COATED_OSMIUM": 80,
        "INTARIAN_PEPPER_ROOT": 80,
    }

    # ASH_COATED_OSMIUM: Fixed fair value logic
    OSMIUM_FAIR = 10000
    OSMIUM_PASSIVE_SIZE = 20
    OSMIUM_FLATTEN_THRESHOLD = 50

    # INTARIAN_PEPPER_ROOT: accumulate 40 units on each of the first 2 ticks
    ROOT_BUY_PER_TICK = 40
    ROOT_NUM_BUY_TICKS = 2

    def bid(self):
        return 15

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

        # Track how many root accumulation ticks have already been used.
        if "root_buy_ticks_done" not in data:
            data["root_buy_ticks_done"] = 0

        for product, order_depth in state.order_depths.items():
            if product not in self.LIMITS:
                continue

            orders: List[Order] = []

            # Need both sides of the book for these strategies.
            if len(order_depth.buy_orders) == 0 or len(order_depth.sell_orders) == 0:
                result[product] = orders
                continue

            limit = self.LIMITS[product]
            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())

            current_position = state.position.get(product, 0)
            net_pos = current_position
            buy_capacity = limit - net_pos
            sell_capacity = limit + net_pos

            def add_buy(price: int, qty: int):
                nonlocal net_pos, buy_capacity, sell_capacity, orders
                qty = int(max(0, min(qty, buy_capacity)))
                if qty > 0:
                    orders.append(Order(product, int(price), qty))
                    net_pos += qty
                    buy_capacity -= qty
                    sell_capacity += qty

            def add_sell(price: int, qty: int):
                nonlocal net_pos, buy_capacity, sell_capacity, orders
                qty = int(max(0, min(qty, sell_capacity)))
                if qty > 0:
                    orders.append(Order(product, int(price), -qty))
                    net_pos -= qty
                    sell_capacity -= qty
                    buy_capacity += qty

            if product == "ASH_COATED_OSMIUM":
                fair_value = self.OSMIUM_FAIR

                # 1) Immediately take favorable trades.
                for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                    if buy_capacity <= 0:
                        break
                    if ask_price < fair_value:
                        add_buy(ask_price, -ask_volume)
                    else:
                        break

                for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
                    if sell_capacity <= 0:
                        break
                    if bid_price > fair_value:
                        add_sell(bid_price, bid_volume)
                    else:
                        break

                # 2) Flatten at exactly fair value if inventory is too skewed.
                if net_pos >= self.OSMIUM_FLATTEN_THRESHOLD and sell_capacity > 0:
                    flatten_qty = min(net_pos, self.OSMIUM_PASSIVE_SIZE)
                    add_sell(fair_value, flatten_qty)
                elif net_pos <= -self.OSMIUM_FLATTEN_THRESHOLD and buy_capacity > 0:
                    flatten_qty = min(-net_pos, self.OSMIUM_PASSIVE_SIZE)
                    add_buy(fair_value, flatten_qty)

                # 3) Passive quoting: overbid / undercut while keeping positive edge.
                improved_bid = best_bid + 1
                improved_ask = best_ask - 1

                allow_buy_quote = net_pos < self.OSMIUM_FLATTEN_THRESHOLD
                allow_sell_quote = net_pos > -self.OSMIUM_FLATTEN_THRESHOLD

                if allow_buy_quote and buy_capacity > 0:
                    if improved_bid < best_ask and improved_bid < fair_value:
                        buy_size = self.OSMIUM_PASSIVE_SIZE
                        if net_pos > 0:
                            buy_size = max(0, buy_size - net_pos // 8)
                        elif net_pos < 0:
                            buy_size = buy_size + (-net_pos) // 8
                        add_buy(improved_bid, buy_size)

                if allow_sell_quote and sell_capacity > 0:
                    if improved_ask > best_bid and improved_ask > fair_value:
                        sell_size = self.OSMIUM_PASSIVE_SIZE
                        if net_pos > 0:
                            sell_size = sell_size + net_pos // 8
                        elif net_pos < 0:
                            sell_size = max(0, sell_size - (-net_pos) // 8)
                        add_sell(improved_ask, sell_size)

            elif product == "INTARIAN_PEPPER_ROOT":
                if buy_capacity > 0:
                    for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                        if buy_capacity <= 0:
                            break

                        take_qty = min(buy_capacity, -ask_volume)
                        if take_qty > 0:
                            add_buy(ask_price, take_qty)

            result[product] = orders

        traderData = json.dumps(data)
        return result, conversions, traderData
