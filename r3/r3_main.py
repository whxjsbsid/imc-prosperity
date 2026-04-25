from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict


class Trader:
    LIMITS = {
        "HYDROGEL_PACK": 80,
    }

    # HYDROGEL_PACK parameters
    # Hydrogel is not clean enough for aggressive fixed-fair market making.
    # Trade only when price is meaningfully away from fair value.
    HYDROGEL_FAIR = 9990.8
    HYDROGEL_EDGE = 31.9
    HYDROGEL_MAX_TAKE_SIZE = 10
    HYDROGEL_FLATTEN_THRESHOLD = 40
    HYDROGEL_FLATTEN_SIZE = 10

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
        1. Use a lower fixed fair value around the historical average.
        2. Buy only extreme cheap asks below fair - edge.
        3. Sell only extreme expensive bids above fair + edge.
        4. Avoid normal passive one-tick market making because Hydrogel is not
           mean reverting strongly enough for that style.
        5. If inventory becomes too skewed, place a small flattening order at fair.
        """
        orders: List[Order] = []

        limit = self.LIMITS[product]
        fair_value = self.HYDROGEL_FAIR
        buy_threshold = fair_value - self.HYDROGEL_EDGE
        sell_threshold = fair_value + self.HYDROGEL_EDGE
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

        # Take only clearly cheap asks.
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if buy_capacity <= 0:
                break
            if ask_price < buy_threshold:
                buy_qty = min(-ask_volume, self.HYDROGEL_MAX_TAKE_SIZE)
                add_buy(ask_price, buy_qty)
            else:
                break

        # Hit only clearly expensive bids.
        for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if sell_capacity <= 0:
                break
            if bid_price > sell_threshold:
                sell_qty = min(bid_volume, self.HYDROGEL_MAX_TAKE_SIZE)
                add_sell(bid_price, sell_qty)
            else:
                break

        # Small inventory flattening only when position is very skewed.
        # This is different from normal passive market making: it only reduces risk.
        if net_pos >= self.HYDROGEL_FLATTEN_THRESHOLD and sell_capacity > 0:
            flatten_qty = min(net_pos, self.HYDROGEL_FLATTEN_SIZE)
            add_sell(fair_value, flatten_qty)
        elif net_pos <= -self.HYDROGEL_FLATTEN_THRESHOLD and buy_capacity > 0:
            flatten_qty = min(-net_pos, self.HYDROGEL_FLATTEN_SIZE)
            add_buy(fair_value, flatten_qty)

        return orders
