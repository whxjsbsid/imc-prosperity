from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict


class Trader:
    LIMITS = {
        "HYDROGEL_PACK": 80,
    }

    # HYDROGEL_PACK parameters
    HYDROGEL_FAIR = 10000
    HYDROGEL_PASSIVE_SIZE = 20
    HYDROGEL_FLATTEN_THRESHOLD = 50

    def bid(self):
        return 3000

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        for product, order_depth in state.order_depths.items():
            if product not in self.LIMITS:
                continue

            if product == "HYDROGEL_PACK":
                result[product] = self.trade_hydrogel(state, product, order_depth)

        traderData = ""
        return result, conversions, traderData

    def trade_hydrogel(
        self,
        state: TradingState,
        product: str,
        order_depth: OrderDepth,
    ) -> List[Order]:
        """
        HYDROGEL logic:
        1. Aggressively take asks below fixed fair value.
        2. Aggressively hit bids above fixed fair value.
        3. If inventory gets too large, place a flattening order at fair value.
        4. If both sides exist, post passive quotes one tick inside the spread.
        """
        orders: List[Order] = []

        limit = self.LIMITS[product]
        fair_value = self.HYDROGEL_FAIR
        current_position = state.position.get(product, 0)

        net_pos = current_position
        buy_capacity = limit - net_pos
        sell_capacity = limit + net_pos

        def add_buy(price: int, qty: int):
            nonlocal net_pos, buy_capacity, sell_capacity
            qty = int(max(0, min(qty, buy_capacity)))
            if qty > 0:
                orders.append(Order(product, int(price), qty))
                net_pos += qty
                buy_capacity -= qty
                sell_capacity += qty

        def add_sell(price: int, qty: int):
            nonlocal net_pos, buy_capacity, sell_capacity
            qty = int(max(0, min(qty, sell_capacity)))
            if qty > 0:
                orders.append(Order(product, int(price), -qty))
                net_pos -= qty
                sell_capacity -= qty
                buy_capacity += qty

        # Take asks below fair value.
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if buy_capacity <= 0:
                break
            if ask_price < fair_value:
                add_buy(ask_price, -ask_volume)
            else:
                break

        # Take bids above fair value.
        for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if sell_capacity <= 0:
                break
            if bid_price > fair_value:
                add_sell(bid_price, bid_volume)
            else:
                break

        # Flatten inventory if position becomes too skewed.
        if net_pos >= self.HYDROGEL_FLATTEN_THRESHOLD and sell_capacity > 0:
            flatten_qty = min(net_pos, self.HYDROGEL_PASSIVE_SIZE)
            add_sell(fair_value, flatten_qty)
        elif net_pos <= -self.HYDROGEL_FLATTEN_THRESHOLD and buy_capacity > 0:
            flatten_qty = min(-net_pos, self.HYDROGEL_PASSIVE_SIZE)
            add_buy(fair_value, flatten_qty)

        # Post one-tick-inside passive quotes if both sides of the book exist.
        if order_depth.buy_orders and order_depth.sell_orders:
            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())

            improved_bid = best_bid + 1
            improved_ask = best_ask - 1

            allow_buy_quote = net_pos < self.HYDROGEL_FLATTEN_THRESHOLD
            allow_sell_quote = net_pos > -self.HYDROGEL_FLATTEN_THRESHOLD

            if allow_buy_quote and buy_capacity > 0:
                if improved_bid < best_ask and improved_bid < fair_value:
                    buy_size = self.HYDROGEL_PASSIVE_SIZE
                    if net_pos > 0:
                        buy_size = max(0, buy_size - net_pos // 8)
                    elif net_pos < 0:
                        buy_size = buy_size + (-net_pos) // 8
                    add_buy(improved_bid, buy_size)

            if allow_sell_quote and sell_capacity > 0:
                if improved_ask > best_bid and improved_ask > fair_value:
                    sell_size = self.HYDROGEL_PASSIVE_SIZE
                    if net_pos > 0:
                        sell_size = sell_size + net_pos // 8
                    elif net_pos < 0:
                        sell_size = max(0, sell_size - (-net_pos) // 8)
                    add_sell(improved_ask, sell_size)

        return orders
