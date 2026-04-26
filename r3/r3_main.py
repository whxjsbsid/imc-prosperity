from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Tuple, Optional
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
    HYDROGEL_MAX_TAKE_SIZE = 100
    HYDROGEL_FLATTEN_THRESHOLD = 200
    HYDROGEL_FLATTEN_SIZE = 50

    # Velvetfruit voucher parameters
    VELVETFRUIT = "VELVETFRUIT_EXTRACT"

    # IV-smile setup: trade mainly the near-the-money vouchers.
    # Deep ITM vouchers behave mostly like the underlying, while far OTM vouchers
    # produce unstable implied vol estimates.
    VOUCHER_STRIKES = {
        "VEV_5000": 5000,
        "VEV_5100": 5100,
        "VEV_5200": 5200,
        "VEV_5300": 5300,
        "VEV_5400": 5400,
        "VEV_5500": 5500,
    }

    # At the start of Round 3 final simulation, TTE is about 5 days.
    OPTION_START_DAYS_TO_EXPIRY = 5.0

    # IV-smile trading controls.
    # Trade only if the option is cheap/rich versus the fitted smile by both
    # price edge and implied-vol edge.
    VOUCHER_MIN_PRICE_EDGE = 3.0
    VOUCHER_PRICE_EDGE_RATIO = 0.009
    VOUCHER_MIN_IV_EDGE = 0.009
    VOUCHER_MAX_TAKE_SIZE = 100
    VOUCHER_SOFT_POSITION_LIMIT = 300

    # Rare delta hedge controls.
    # Trigger = how much unhedged option delta we tolerate.
    # Clip = max Velvetfruit quantity to trade in one tick if hedge triggers.
    HEDGE_ENABLED = True
    HEDGE_TRIGGER_DELTA = 160
    HEDGE_CLIP_SIZE = 30

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

        voucher_result = self.trade_vouchers_iv_smile(state)
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
        if net_pos >= self.HYDROGEL_FLATTEN_THRESHOLD and sell_capacity > 0:
            flatten_qty = min(net_pos, self.HYDROGEL_FLATTEN_SIZE)
            add_sell(fair_value, flatten_qty)
        elif net_pos <= -self.HYDROGEL_FLATTEN_THRESHOLD and buy_capacity > 0:
            flatten_qty = min(-net_pos, self.HYDROGEL_FLATTEN_SIZE)
            add_buy(fair_value, flatten_qty)

        return orders

    def trade_vouchers_iv_smile(self, state: TradingState) -> Dict[str, List[Order]]:
        """
        VEV logic:
        1. Use VELVETFRUIT_EXTRACT mid price as the underlying price.
        2. Convert each voucher mid price into implied volatility.
        3. For each voucher, fit a quadratic IV smile using the OTHER vouchers only.
           This is leave-one-out fitting, so the option being judged does not pull
           its own fair value back toward its current market mid.
        4. Convert leave-one-out fair IV back into a Black-Scholes fair price.
        5. Trade only clear IV/price deviations.
        6. Hedge Velvetfruit delta only rarely, to avoid spread-cost bleeding.
        """
        result: Dict[str, List[Order]] = {}

        if self.VELVETFRUIT not in state.order_depths:
            return result

        velvet_depth = state.order_depths[self.VELVETFRUIT]
        spot = self.get_mid_price(velvet_depth)
        if spot is None or spot <= 0:
            return result

        time_to_expiry = self.time_to_expiry_years(state)

        # Store each usable voucher's IV point as:
        # product -> (log_moneyness, implied_vol)
        iv_points_by_product: Dict[str, Tuple[float, float]] = {}

        for product, strike in self.VOUCHER_STRIKES.items():
            if product not in state.order_depths:
                continue

            mid = self.get_mid_price(state.order_depths[product])
            if mid is None:
                continue

            implied_vol = self.implied_vol_call(spot, strike, time_to_expiry, mid)
            if implied_vol is None:
                continue

            # Filter obvious bad IV points that can distort the smile.
            if implied_vol < 0.05 or implied_vol > 2.00:
                continue

            log_moneyness = math.log(strike / spot)
            iv_points_by_product[product] = (log_moneyness, implied_vol)

        # Leave-one-out quadratic needs at least 3 OTHER usable IV points.
        # Therefore we need at least 4 total points to judge any one voucher.
        if len(iv_points_by_product) < 4:
            return result

        option_delta_from_new_trades = 0.0
        deltas: Dict[str, float] = {}

        for product, strike in self.VOUCHER_STRIKES.items():
            if product not in state.order_depths:
                continue
            if product not in iv_points_by_product:
                continue

            # Leave-one-out: exclude the current voucher from the smile fit.
            other_points = [
                point
                for other_product, point in iv_points_by_product.items()
                if other_product != product
            ]

            if len(other_points) < 3:
                continue

            smile_coeffs = self.fit_quadratic(other_points)
            if smile_coeffs is None:
                continue

            x = math.log(strike / spot)
            fair_iv = self.eval_quadratic(smile_coeffs, x)
            fair_iv = max(0.05, min(2.00, fair_iv))
            fair_price = self.black_scholes_call(spot, strike, time_to_expiry, fair_iv)
            fair_delta = self.option_delta(spot, strike, time_to_expiry, fair_iv)

            orders, traded_delta = self.trade_one_voucher_from_smile(
                state=state,
                product=product,
                order_depth=state.order_depths[product],
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                fair_iv=fair_iv,
                fair_price=fair_price,
                fair_delta=fair_delta,
            )

            if orders:
                result[product] = orders

            option_delta_from_new_trades += traded_delta
            deltas[product] = fair_delta

        hedge_orders = self.hedge_velvetfruit_delta_rare(
            state,
            velvet_depth,
            deltas,
            option_delta_from_new_trades,
        )
        if hedge_orders:
            result[self.VELVETFRUIT] = hedge_orders

        return result

    def trade_one_voucher_from_smile(
        self,
        state: TradingState,
        product: str,
        order_depth: OrderDepth,
        spot: float,
        strike: int,
        time_to_expiry: float,
        fair_iv: float,
        fair_price: float,
        fair_delta: float,
    ) -> Tuple[List[Order], float]:
        orders: List[Order] = []

        if not order_depth.buy_orders and not order_depth.sell_orders:
            return orders, 0.0

        limit = self.LIMITS[product]
        current_position = state.position.get(product, 0)
        net_pos = current_position
        buy_capacity = limit - net_pos
        sell_capacity = limit + net_pos
        traded_delta = 0.0

        price_edge = max(
            self.VOUCHER_MIN_PRICE_EDGE,
            fair_price * self.VOUCHER_PRICE_EDGE_RATIO,
        )

        # Avoid adding too aggressively to already large same-direction positions.
        allow_buy = net_pos < self.VOUCHER_SOFT_POSITION_LIMIT
        allow_sell = net_pos > -self.VOUCHER_SOFT_POSITION_LIMIT

        def add_buy(price: int, qty: int):
            nonlocal net_pos, buy_capacity, sell_capacity, traded_delta
            qty = int(max(0, min(qty, buy_capacity)))
            if qty > 0:
                orders.append(Order(product, int(price), qty))
                net_pos += qty
                buy_capacity -= qty
                sell_capacity += qty
                traded_delta += qty * fair_delta

        def add_sell(price: int, qty: int):
            nonlocal net_pos, buy_capacity, sell_capacity, traded_delta
            qty = int(max(0, min(qty, sell_capacity)))
            if qty > 0:
                orders.append(Order(product, int(price), -qty))
                net_pos -= qty
                sell_capacity -= qty
                buy_capacity += qty
                traded_delta -= qty * fair_delta

        # Buy asks that are cheap versus fitted smile fair value.
        if allow_buy and buy_capacity > 0 and order_depth.sell_orders:
            for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                if buy_capacity <= 0:
                    break

                ask_iv = self.implied_vol_call(spot, strike, time_to_expiry, ask_price)
                iv_is_cheap = ask_iv is not None and ask_iv < fair_iv - self.VOUCHER_MIN_IV_EDGE
                price_is_cheap = ask_price < fair_price - price_edge

                if price_is_cheap and iv_is_cheap:
                    buy_qty = min(-ask_volume, self.VOUCHER_MAX_TAKE_SIZE)
                    add_buy(ask_price, buy_qty)
                else:
                    break

        # Sell bids that are rich versus fitted smile fair value.
        if allow_sell and sell_capacity > 0 and order_depth.buy_orders:
            for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
                if sell_capacity <= 0:
                    break

                bid_iv = self.implied_vol_call(spot, strike, time_to_expiry, bid_price)
                iv_is_rich = bid_iv is not None and bid_iv > fair_iv + self.VOUCHER_MIN_IV_EDGE
                price_is_rich = bid_price > fair_price + price_edge

                if price_is_rich and iv_is_rich:
                    sell_qty = min(bid_volume, self.VOUCHER_MAX_TAKE_SIZE)
                    add_sell(bid_price, sell_qty)
                else:
                    break

        return orders, traded_delta

    def hedge_velvetfruit_delta_rare(
        self,
        state: TradingState,
        order_depth: OrderDepth,
        deltas: Dict[str, float],
        option_delta_from_new_trades: float,
    ) -> List[Order]:
        """
        Rare hedge only. Constant hedging bled too much in backtests because it
        repeatedly crossed the Velvetfruit spread. This hedge only activates when
        option delta exposure gets very large.
        """
        orders: List[Order] = []
        if not self.HEDGE_ENABLED:
            return orders

        product = self.VELVETFRUIT
        limit = self.LIMITS[product]

        option_delta_position = option_delta_from_new_trades
        for voucher, delta in deltas.items():
            option_delta_position += state.position.get(voucher, 0) * delta

        current_velvet_position = state.position.get(product, 0)
        current_total_delta = current_velvet_position + option_delta_position

        if abs(current_total_delta) < self.HEDGE_TRIGGER_DELTA:
            return orders

        # We want to push total delta back toward zero.
        target_velvet_position = int(round(-option_delta_position))
        target_velvet_position = max(-limit, min(limit, target_velvet_position))
        qty_needed = target_velvet_position - current_velvet_position

        if qty_needed > 0:
            buy_capacity = limit - current_velvet_position
            remaining = min(qty_needed, buy_capacity, self.HEDGE_CLIP_SIZE)
            for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                if remaining <= 0:
                    break
                buy_qty = min(-ask_volume, remaining)
                if buy_qty > 0:
                    orders.append(Order(product, int(ask_price), int(buy_qty)))
                    remaining -= buy_qty
        elif qty_needed < 0:
            sell_capacity = limit + current_velvet_position
            remaining = min(-qty_needed, sell_capacity, self.HEDGE_CLIP_SIZE)
            for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
                if remaining <= 0:
                    break
                sell_qty = min(bid_volume, remaining)
                if sell_qty > 0:
                    orders.append(Order(product, int(bid_price), -int(sell_qty)))
                    remaining -= sell_qty

        return orders

    def get_mid_price(self, order_depth: OrderDepth) -> Optional[float]:
        if order_depth.buy_orders and order_depth.sell_orders:
            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())
            return (best_bid + best_ask) / 2
        if order_depth.buy_orders:
            return float(max(order_depth.buy_orders.keys()))
        if order_depth.sell_orders:
            return float(min(order_depth.sell_orders.keys()))
        return None

    def time_to_expiry_years(self, state: TradingState) -> float:
        # Round 3 starts with about 5 days to expiry. The timestamp usually
        # runs from 0 to around 1_000_000 over the simulation day, so subtract
        # the fraction of a day that has passed.
        day_progress = max(0.0, min(1.0, state.timestamp / 1_000_000.0))
        days_left = max(0.01, self.OPTION_START_DAYS_TO_EXPIRY - day_progress)
        return days_left / 365.0

    def black_scholes_call(
        self,
        spot: float,
        strike: int,
        time_to_expiry: float,
        sigma: float,
    ) -> float:
        if time_to_expiry <= 0 or sigma <= 0:
            return max(spot - strike, 0.0)

        sqrt_t = math.sqrt(time_to_expiry)
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * time_to_expiry) / (
            sigma * sqrt_t
        )
        d2 = d1 - sigma * sqrt_t
        return spot * self.normal_cdf(d1) - strike * self.normal_cdf(d2)

    def option_delta(
        self,
        spot: float,
        strike: int,
        time_to_expiry: float,
        sigma: float,
    ) -> float:
        if time_to_expiry <= 0 or sigma <= 0:
            return 1.0 if spot > strike else 0.0

        sqrt_t = math.sqrt(time_to_expiry)
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * time_to_expiry) / (
            sigma * sqrt_t
        )
        return self.normal_cdf(d1)

    def implied_vol_call(
        self,
        spot: float,
        strike: int,
        time_to_expiry: float,
        market_price: float,
    ) -> Optional[float]:
        if spot <= 0 or strike <= 0 or time_to_expiry <= 0 or market_price <= 0:
            return None

        intrinsic = max(spot - strike, 0.0)
        if market_price <= intrinsic + 0.01:
            return None
        if market_price >= spot:
            return None

        low = 0.0001
        high = 5.0
        low_price = self.black_scholes_call(spot, strike, time_to_expiry, low)
        high_price = self.black_scholes_call(spot, strike, time_to_expiry, high)

        if market_price < low_price or market_price > high_price:
            return None

        for _ in range(40):
            mid = (low + high) / 2
            mid_price = self.black_scholes_call(spot, strike, time_to_expiry, mid)
            if mid_price < market_price:
                low = mid
            else:
                high = mid

        return (low + high) / 2

    def fit_quadratic(self, points: List[Tuple[float, float]]) -> Optional[Tuple[float, float, float]]:
        # Fit y = a + b*x + c*x^2 using normal equations.
        n = len(points)
        if n < 3:
            return None

        sx = sum(x for x, _ in points)
        sx2 = sum(x * x for x, _ in points)
        sx3 = sum(x * x * x for x, _ in points)
        sx4 = sum(x * x * x * x for x, _ in points)
        sy = sum(y for _, y in points)
        sxy = sum(x * y for x, y in points)
        sx2y = sum(x * x * y for x, y in points)

        matrix = [
            [float(n), sx, sx2, sy],
            [sx, sx2, sx3, sxy],
            [sx2, sx3, sx4, sx2y],
        ]

        return self.solve_3x3_augmented(matrix)

    def solve_3x3_augmented(self, matrix: List[List[float]]) -> Optional[Tuple[float, float, float]]:
        # Gaussian elimination for a 3x4 augmented matrix.
        for col in range(3):
            pivot_row = col
            for row in range(col + 1, 3):
                if abs(matrix[row][col]) > abs(matrix[pivot_row][col]):
                    pivot_row = row

            if abs(matrix[pivot_row][col]) < 1e-12:
                return None

            if pivot_row != col:
                matrix[col], matrix[pivot_row] = matrix[pivot_row], matrix[col]

            pivot = matrix[col][col]
            for j in range(col, 4):
                matrix[col][j] /= pivot

            for row in range(3):
                if row == col:
                    continue
                factor = matrix[row][col]
                for j in range(col, 4):
                    matrix[row][j] -= factor * matrix[col][j]

        return matrix[0][3], matrix[1][3], matrix[2][3]

    def eval_quadratic(self, coeffs: Tuple[float, float, float], x: float) -> float:
        a, b, c = coeffs
        return a + b * x + c * x * x

    def normal_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
