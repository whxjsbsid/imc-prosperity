from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict


class Trader:
    LIMITS = {
        "HYDROGEL_PACK": 200,
        "VELVETFRUIT_EXTRACT": 200,
    }

    # Hydrogel is not clean enough for aggressive fixed-fair market making
    # Trade only when price is meaningfully away from fair value
    HYDROGEL_FAIR = 9990.8
    HYDROGEL_EDGE = 31.94 * 0.9
    HYDROGEL_MAX_TAKE_SIZE = 100

    # Same style as Hydrogel: fixed fair value + extreme-only taking
    VELVETFRUIT_FAIR = 5250.1
    VELVETFRUIT_EDGE = 15.63 * 1.4
    VELVETFRUIT_MAX_TAKE_SIZE = 100

    def bid(self):
        return 3000

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        if "HYDROGEL_PACK" in state.order_depths:
            result["HYDROGEL_PACK"] = self.trade_hydrogel(
                state,
                "HYDROGEL_PACK",
                state.order_depths["HYDROGEL_PACK"],
            )

        if "VELVETFRUIT_EXTRACT" in state.order_depths:
            result["VELVETFRUIT_EXTRACT"] = self.trade_velvetfruit(
                state,
                "VELVETFRUIT_EXTRACT",
                state.order_depths["VELVETFRUIT_EXTRACT"],
            )

        traderData = ""
        return result, conversions, traderData

        # HYDROGEL logic:
        # 1. Use a lower fixed fair value around the historical average
        # 2. Buy only extreme cheap asks below fair - edge
        # 3. Sell only extreme expensive bids above fair + edge

    def trade_hydrogel(
        self,
        state: TradingState,
        product: str,
        order_depth: OrderDepth,
    ) -> List[Order]:

        return self.trade_extreme_mean_reversion(
            state=state,
            product=product,
            order_depth=order_depth,
            fair_value=self.HYDROGEL_FAIR,
            edge=self.HYDROGEL_EDGE,
            max_take_size=self.HYDROGEL_MAX_TAKE_SIZE,
        )

        
    # VELVETFRUIT logic:
    # 1. Use a fixed fair value around the historical average.
    # 2. Buy only cheap asks below fair - edge.
    # 3. Sell only expensive bids above fair + edge.
    # 4. No vouchers, no option hedging, no passive market making.
    def trade_velvetfruit(
        self,
        state: TradingState,
        product: str,
        order_depth: OrderDepth,
    ) -> List[Order]:

        return self.trade_extreme_mean_reversion(
            state=state,
            product=product,
            order_depth=order_depth,
            fair_value=self.VELVETFRUIT_FAIR,
            edge=self.VELVETFRUIT_EDGE,
            max_take_size=self.VELVETFRUIT_MAX_TAKE_SIZE,
        )

    # Shared extreme-only mean reversion logic
    def trade_extreme_mean_reversion(
        self,
        state: TradingState,
        product: str,
        order_depth: OrderDepth,
        fair_value: float,
        edge: float,
        max_take_size: int,
    ) -> List[Order]:

        orders: List[Order] = []

        limit = self.LIMITS[product]
        buy_threshold = fair_value - edge
        sell_threshold = fair_value + edge
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

        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if buy_capacity <= 0:
                break
            if ask_price < buy_threshold:
                buy_qty = min(-ask_volume, max_take_size)
                add_buy(ask_price, buy_qty)
            else:
                break

        for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if sell_capacity <= 0:
                break
            if bid_price > sell_threshold:
                sell_qty = min(bid_volume, max_take_size)
                add_sell(bid_price, sell_qty)
            else:
                break

        return orders
