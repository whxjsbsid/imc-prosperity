from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Tuple
import math


class Trader:
    LIMITS = {
        "HYDROGEL_PACK": 200,
        "VELVETFRUIT_EXTRACT": 200,
        "VEV_4000": 300,
        "VEV_4500": 300,
        "VEV_5000": 300,
        "VEV_5100": 300,
        "VEV_5200": 300,
        "VEV_5300": 300,
        "VEV_5400": 300,
        "VEV_5500": 300,
        "VEV_6000": 300,
        "VEV_6500": 300,
    }

    # HYDROGEL_PACK parameters
    # Hydrogel is not clean enough for aggressive fixed-fair market making.
    # Trade only when price is meaningfully away from fair value.
    HYDROGEL_FAIR = 9990.8
    HYDROGEL_EDGE = 31.9 * 0.9
    HYDROGEL_MAX_TAKE_SIZE = 50
    HYDROGEL_FLATTEN_THRESHOLD = 150
    HYDROGEL_FLATTEN_SIZE = 25

    # Velvetfruit voucher parameters
    VELVETFRUIT = "VELVETFRUIT_EXTRACT"
    # Safer setup: only trade the near-the-money vouchers.
    # Deep ITM vouchers mostly behave like Velvetfruit itself, while far OTM
    # vouchers are too noisy for this simple model.
    VOUCHER_STRIKES = {
        "VEV_5000": 5000,
        "VEV_5100": 5100,
        "VEV_5200": 5200,
        "VEV_5300": 5300,
        "VEV_5400": 5400,
    }

    # Round 3 final simulation starts with around 5 days to expiry.
    OPTION_DAYS_TO_EXPIRY = 5.0
    OPTION_SIGMA = 0.35

    # Much stricter voucher entry rules.
    # This avoids trading tiny model gaps that get eaten by spread + hedging cost.
    VOUCHER_MIN_EDGE = 10.0
    VOUCHER_EDGE_RATIO = 0.06
    VOUCHER_MAX_TAKE_SIZE = 100

    # Hedge less often and with smaller clips to reduce Velvetfruit spread cost.
    HEDGE_MIN_QTY = 40
    HEDGE_MAX_SIZE = 20

    def bid(self):
        return 3000

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        # Keep Hydrogel separate because it is a normal extreme-only mean reversion trade.
        if "HYDROGEL_PACK" in state.order_depths:
            result["HYDROGEL_PACK"] = self.trade_hydrogel(
                state,
                "HYDROGEL_PACK",
                state.order_depths["HYDROGEL_PACK"],
            )

        # Trade VEV vouchers using Black-Scholes fair value, then hedge with Velvetfruit.
        voucher_result = self.trade_vouchers_with_hedge(state)
        for product, orders in voucher_result.items():
            if orders:
                result[product] = orders

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

    def trade_vouchers_with_hedge(self, state: TradingState) -> Dict[str, List[Order]]:
        """
        VEV logic:
        1. Use VELVETFRUIT_EXTRACT mid price as the underlying price.
        2. Use Black-Scholes call value for each VEV voucher.
        3. Buy vouchers that are clearly below fair value.
        4. Sell vouchers that are clearly above fair value.
        5. Hedge total option delta using VELVETFRUIT_EXTRACT.
        """
        result: Dict[str, List[Order]] = {}

        if self.VELVETFRUIT not in state.order_depths:
            return result

        velvet_depth = state.order_depths[self.VELVETFRUIT]
        underlying_mid = self.get_mid_price(velvet_depth)
        if underlying_mid is None:
            return result

        option_delta_from_new_trades = 0.0
        deltas: Dict[str, float] = {}

        for product, strike in self.VOUCHER_STRIKES.items():
            if product not in state.order_depths:
                continue

            order_depth = state.order_depths[product]
            orders, traded_delta = self.trade_one_voucher(
                state,
                product,
                order_depth,
                underlying_mid,
                strike,
            )

            if orders:
                result[product] = orders

            option_delta_from_new_trades += traded_delta
            deltas[product] = self.option_delta(underlying_mid, strike)

        hedge_orders = self.hedge_velvetfruit_delta(
            state,
            velvet_depth,
            underlying_mid,
            deltas,
            option_delta_from_new_trades,
        )
        if hedge_orders:
            result[self.VELVETFRUIT] = hedge_orders

        return result

    def trade_one_voucher(
        self,
        state: TradingState,
        product: str,
        order_depth: OrderDepth,
        underlying_mid: float,
        strike: int,
    ) -> Tuple[List[Order], float]:
        orders: List[Order] = []

        limit = self.LIMITS[product]
        fair_value = self.option_fair_value(underlying_mid, strike)
        delta = self.option_delta(underlying_mid, strike)
        edge = max(self.VOUCHER_MIN_EDGE, fair_value * self.VOUCHER_EDGE_RATIO)

        current_position = state.position.get(product, 0)
        net_pos = current_position
        buy_capacity = limit - net_pos
        sell_capacity = limit + net_pos
        traded_delta = 0.0

        def add_buy(price: int, qty: int):
            nonlocal net_pos, buy_capacity, sell_capacity, traded_delta
            qty = int(max(0, min(qty, buy_capacity)))
            if qty > 0:
                orders.append(Order(product, int(price), qty))
                net_pos += qty
                buy_capacity -= qty
                sell_capacity += qty
                traded_delta += qty * delta

        def add_sell(price: int, qty: int):
            nonlocal net_pos, buy_capacity, sell_capacity, traded_delta
            qty = int(max(0, min(qty, sell_capacity)))
            if qty > 0:
                orders.append(Order(product, int(price), -qty))
                net_pos -= qty
                sell_capacity -= qty
                buy_capacity += qty
                traded_delta -= qty * delta

        # Buy asks that are clearly cheap versus model fair value.
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if buy_capacity <= 0:
                break
            if ask_price < fair_value - edge:
                buy_qty = min(-ask_volume, self.VOUCHER_MAX_TAKE_SIZE)
                add_buy(ask_price, buy_qty)
            else:
                break

        # Sell bids that are clearly rich versus model fair value.
        for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if sell_capacity <= 0:
                break
            if bid_price > fair_value + edge:
                sell_qty = min(bid_volume, self.VOUCHER_MAX_TAKE_SIZE)
                add_sell(bid_price, sell_qty)
            else:
                break

        return orders, traded_delta

    def hedge_velvetfruit_delta(
        self,
        state: TradingState,
        order_depth: OrderDepth,
        underlying_mid: float,
        deltas: Dict[str, float],
        option_delta_from_new_trades: float,
    ) -> List[Order]:
        """
        If total option delta is positive, sell Velvetfruit.
        If total option delta is negative, buy Velvetfruit.
        """
        orders: List[Order] = []
        product = self.VELVETFRUIT
        limit = self.LIMITS[product]

        option_delta_position = option_delta_from_new_trades
        for voucher, delta in deltas.items():
            option_delta_position += state.position.get(voucher, 0) * delta

        current_velvet_position = state.position.get(product, 0)
        target_velvet_position = int(round(-option_delta_position))
        target_velvet_position = max(-limit, min(limit, target_velvet_position))

        qty_needed = target_velvet_position - current_velvet_position
        if abs(qty_needed) < self.HEDGE_MIN_QTY:
            return orders

        if qty_needed > 0:
            # Need to buy Velvetfruit to reduce short delta.
            buy_capacity = limit - current_velvet_position
            remaining = min(qty_needed, buy_capacity, self.HEDGE_MAX_SIZE)
            for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                if remaining <= 0:
                    break
                buy_qty = min(-ask_volume, remaining)
                if buy_qty > 0:
                    orders.append(Order(product, int(ask_price), int(buy_qty)))
                    remaining -= buy_qty
        else:
            # Need to sell Velvetfruit to reduce long delta.
            sell_capacity = limit + current_velvet_position
            remaining = min(-qty_needed, sell_capacity, self.HEDGE_MAX_SIZE)
            for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
                if remaining <= 0:
                    break
                sell_qty = min(bid_volume, remaining)
                if sell_qty > 0:
                    orders.append(Order(product, int(bid_price), -int(sell_qty)))
                    remaining -= sell_qty

        return orders

    def get_mid_price(self, order_depth: OrderDepth):
        if order_depth.buy_orders and order_depth.sell_orders:
            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())
            return (best_bid + best_ask) / 2
        if order_depth.buy_orders:
            return max(order_depth.buy_orders.keys())
        if order_depth.sell_orders:
            return min(order_depth.sell_orders.keys())
        return None

    def option_fair_value(self, spot: float, strike: int) -> float:
        time_to_expiry = max(self.OPTION_DAYS_TO_EXPIRY / 365.0, 1e-6)
        sigma = self.OPTION_SIGMA

        # Deep ITM vouchers behave almost exactly like spot - strike.
        # This avoids tiny model noise causing bad trades in VEV_4000/4500.
        if strike <= 4500:
            return max(spot - strike, 0.0)

        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * time_to_expiry) / (
            sigma * math.sqrt(time_to_expiry)
        )
        d2 = d1 - sigma * math.sqrt(time_to_expiry)

        return spot * self.normal_cdf(d1) - strike * self.normal_cdf(d2)

    def option_delta(self, spot: float, strike: int) -> float:
        time_to_expiry = max(self.OPTION_DAYS_TO_EXPIRY / 365.0, 1e-6)
        sigma = self.OPTION_SIGMA

        if strike <= 4500:
            return 1.0
        if strike >= 6000:
            return 0.0

        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * time_to_expiry) / (
            sigma * math.sqrt(time_to_expiry)
        )
        return self.normal_cdf(d1)

    def normal_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
