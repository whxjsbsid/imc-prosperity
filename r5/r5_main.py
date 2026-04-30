import json
from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Any, Optional


class Trader:
    # Round 5: every listed product has position limit 10.
    DEFAULT_LIMIT = 10

    LIMITS = {
        "GALAXY_SOUNDS_DARK_MATTER": 10,
        "GALAXY_SOUNDS_BLACK_HOLES": 10,
        "GALAXY_SOUNDS_PLANETARY_RINGS": 10,
        "GALAXY_SOUNDS_SOLAR_WINDS": 10,
        "GALAXY_SOUNDS_SOLAR_FLAMES": 10,
        "SLEEP_POD_SUEDE": 10,
        "SLEEP_POD_LAMB_WOOL": 10,
        "SLEEP_POD_POLYESTER": 10,
        "SLEEP_POD_NYLON": 10,
        "SLEEP_POD_COTTON": 10,
        "MICROCHIP_CIRCLE": 10,
        "MICROCHIP_OVAL": 10,
        "MICROCHIP_SQUARE": 10,
        "MICROCHIP_RECTANGLE": 10,
        "MICROCHIP_TRIANGLE": 10,
        "PEBBLES_XS": 10,
        "PEBBLES_S": 10,
        "PEBBLES_M": 10,
        "PEBBLES_L": 10,
        "PEBBLES_XL": 10,
        "ROBOT_VACUUMING": 10,
        "ROBOT_MOPPING": 10,
        "ROBOT_DISHES": 10,
        "ROBOT_LAUNDRY": 10,
        "ROBOT_IRONING": 10,
        "UV_VISOR_YELLOW": 10,
        "UV_VISOR_AMBER": 10,
        "UV_VISOR_ORANGE": 10,
        "UV_VISOR_RED": 10,
        "UV_VISOR_MAGENTA": 10,
        "TRANSLATOR_SPACE_GRAY": 10,
        "TRANSLATOR_ASTRO_BLACK": 10,
        "TRANSLATOR_ECLIPSE_CHARCOAL": 10,
        "TRANSLATOR_GRAPHITE_MIST": 10,
        "TRANSLATOR_VOID_BLUE": 10,
        "PANEL_1X2": 10,
        "PANEL_2X2": 10,
        "PANEL_1X4": 10,
        "PANEL_2X4": 10,
        "PANEL_4X4": 10,
        "OXYGEN_SHAKE_MORNING_BREATH": 10,
        "OXYGEN_SHAKE_EVENING_BREATH": 10,
        "OXYGEN_SHAKE_MINT": 10,
        "OXYGEN_SHAKE_CHOCOLATE": 10,
        "OXYGEN_SHAKE_GARLIC": 10,
        "SNACKPACK_CHOCOLATE": 10,
        "SNACKPACK_VANILLA": 10,
        "SNACKPACK_PISTACHIO": 10,
        "SNACKPACK_STRAWBERRY": 10,
        "SNACKPACK_RASPBERRY": 10,
    }

    # Pair-sum strategies.
    PAIR_SUM_CONFIGS = [
        {
            "key": "snack_pist_straw_extreme",
            "p1": "SNACKPACK_PISTACHIO",
            "p2": "SNACKPACK_STRAWBERRY",
            "alpha": 0.01,
            "edge": 120.0,
            "max_take_size": 10,
        },
    ]

    # One-tick jump reversal strategies.
    # If mid jumps up by threshold, sell the best bid.
    # If mid drops by threshold, buy the best ask.
    ONE_TICK_REVERSION_CONFIGS = {
        "ROBOT_DISHES": {
            "threshold": 32.0,
            "max_take_size": 10,
        },
        "ROBOT_IRONING": {
            "threshold": 30.0,
            "max_take_size": 10,
        },
        "OXYGEN_SHAKE_CHOCOLATE": {
            "threshold": 26.0,
            "max_take_size": 10,
        },
        "OXYGEN_SHAKE_EVENING_BREATH": {
            "threshold": 30.0,
            "max_take_size": 10,
        },
        "TRANSLATOR_SPACE_GRAY": {
            "threshold": 28.0,
            "max_take_size": 10,
        },
        "UV_VISOR_ORANGE": {
            "threshold": 27.0,
            "max_take_size": 10,
        },
        "GALAXY_SOUNDS_DARK_MATTER": {
            "threshold": 32.0,
            "max_take_size": 10, 
        },
        "GALAXY_SOUNDS_SOLAR_WINDS": {
            "threshold": 36.0,
            "max_take_size": 10, 
        },
        "PEBBLES_M": {
            "threshold": 38.0,
            "max_take_size": 10, 
        },
        "PEBBLES_S": {
            "threshold": 50.0,
            "max_take_size": 10,
        },
        "MICROCHIP_TRIANGLE": {
            "threshold": 50.0,
            "max_take_size": 10, 
        },
    }

    def bid(self):
        # Ignored outside the bidding round, but safe to leave here.
        return 3000

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        data = self.decode_trader_data(state.traderData)
        if "pair_means" not in data or not isinstance(data.get("pair_means"), dict):
            data["pair_means"] = {}
        if "prev_mids" not in data or not isinstance(data.get("prev_mids"), dict):
            data["prev_mids"] = {}

        # planned_position tracks worst-case position after all orders sent this
        # tick. This prevents strategies from jointly breaching position limits.
        planned_position: Dict[str, int] = {}
        for product in state.order_depths:
            planned_position[product] = int(state.position.get(product, 0))

        for config in self.PAIR_SUM_CONFIGS:
            self.trade_pair_sum(
                state=state,
                result=result,
                planned_position=planned_position,
                data=data,
                key=config["key"],
                p1=config["p1"],
                p2=config["p2"],
                alpha=float(config["alpha"]),
                edge=float(config["edge"]),
                max_take_size=int(config["max_take_size"]),
            )

        for product, config in self.ONE_TICK_REVERSION_CONFIGS.items():
            self.trade_one_tick_reversion(
                state=state,
                result=result,
                planned_position=planned_position,
                data=data,
                product=product,
                threshold=float(config["threshold"]),
                max_take_size=int(config["max_take_size"]),
            )

        traderData = self.encode_trader_data(data)
        return result, conversions, traderData

    # ------------------------------------------------------------------
    # traderData helpers
    # ------------------------------------------------------------------
    def decode_trader_data(self, trader_data: str) -> Dict[str, Any]:
        if not trader_data:
            return {}
        try:
            decoded = json.loads(trader_data)
            if isinstance(decoded, dict):
                return decoded
        except Exception:
            pass
        return {}

    def encode_trader_data(self, data: Dict[str, Any]) -> str:
        try:
            return json.dumps(data, separators=(",", ":"))
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Strategy logic
    # ------------------------------------------------------------------
    def trade_pair_sum(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        planned_position: Dict[str, int],
        data: Dict[str, Any],
        key: str,
        p1: str,
        p2: str,
        alpha: float,
        edge: float,
        max_take_size: int,
    ) -> None:
        """
        Rolling pair-sum relative value.

        If p1 + p2 is normally stable, then:
        - fair(p1) = rolling_mean(p1 + p2) - mid(p2)
        - fair(p2) = rolling_mean(p1 + p2) - mid(p1)
        """
        if p1 not in state.order_depths or p2 not in state.order_depths:
            return

        mid1 = self.get_mid_price(state.order_depths[p1])
        mid2 = self.get_mid_price(state.order_depths[p2])
        if mid1 is None or mid2 is None:
            return

        pair_means = data["pair_means"]
        current_sum = mid1 + mid2

        old_mean_raw = pair_means.get(key)
        if old_mean_raw is None:
            pair_means[key] = current_sum
            return

        pair_mean = self.safe_float(old_mean_raw, current_sum)

        fair1 = pair_mean - mid2
        fair2 = pair_mean - mid1

        self.trade_around_fair(
            state=state,
            result=result,
            planned_position=planned_position,
            product=p1,
            fair_value=fair1,
            edge=edge,
            max_take_size=max_take_size,
        )
        self.trade_around_fair(
            state=state,
            result=result,
            planned_position=planned_position,
            product=p2,
            fair_value=fair2,
            edge=edge,
            max_take_size=max_take_size,
        )

        pair_means[key] = (1.0 - alpha) * pair_mean + alpha * current_sum

    def trade_one_tick_reversion(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        planned_position: Dict[str, int],
        data: Dict[str, Any],
        product: str,
        threshold: float,
        max_take_size: int,
    ) -> None:
        """
        One-tick jump reversal.

        - large up move from previous tick -> short the best bid;
        - large down move from previous tick -> buy the best ask.
        """
        if product not in state.order_depths:
            return

        order_depth = state.order_depths[product]
        mid = self.get_mid_price(order_depth)
        if mid is None:
            return

        prev_mids = data["prev_mids"]
        prev_mid_raw = prev_mids.get(product)

        if prev_mid_raw is not None:
            prev_mid = self.safe_float(prev_mid_raw, mid)
            delta = mid - prev_mid

            if delta >= threshold:
                self.sell_best_bid(
                    result=result,
                    planned_position=planned_position,
                    product=product,
                    order_depth=order_depth,
                    max_take_size=max_take_size,
                )
            elif delta <= -threshold:
                self.buy_best_ask(
                    result=result,
                    planned_position=planned_position,
                    product=product,
                    order_depth=order_depth,
                    max_take_size=max_take_size,
                )

        prev_mids[product] = mid

    # ------------------------------------------------------------------
    # Shared execution logic
    # ------------------------------------------------------------------
    def trade_around_fair(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        planned_position: Dict[str, int],
        product: str,
        fair_value: float,
        edge: float,
        max_take_size: int,
    ) -> None:
        if product not in state.order_depths:
            return

        order_depth = state.order_depths[product]
        buy_threshold = fair_value - edge
        sell_threshold = fair_value + edge

        # 1) Take clearly cheap asks.
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if ask_price < buy_threshold:
                qty_available = abs(int(ask_volume))
                self.add_buy_order(
                    result=result,
                    planned_position=planned_position,
                    product=product,
                    price=int(ask_price),
                    quantity=min(qty_available, max_take_size),
                )
            else:
                break

        # 2) Hit clearly expensive bids.
        for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if bid_price > sell_threshold:
                qty_available = abs(int(bid_volume))
                self.add_sell_order(
                    result=result,
                    planned_position=planned_position,
                    product=product,
                    price=int(bid_price),
                    quantity=min(qty_available, max_take_size),
                )
            else:
                break

    def buy_best_ask(
        self,
        result: Dict[str, List[Order]],
        planned_position: Dict[str, int],
        product: str,
        order_depth: OrderDepth,
        max_take_size: int,
    ) -> None:
        if len(order_depth.sell_orders) == 0:
            return
        best_ask, best_ask_volume = min(order_depth.sell_orders.items())
        self.add_buy_order(
            result=result,
            planned_position=planned_position,
            product=product,
            price=int(best_ask),
            quantity=min(abs(int(best_ask_volume)), max_take_size),
        )

    def sell_best_bid(
        self,
        result: Dict[str, List[Order]],
        planned_position: Dict[str, int],
        product: str,
        order_depth: OrderDepth,
        max_take_size: int,
    ) -> None:
        if len(order_depth.buy_orders) == 0:
            return
        best_bid, best_bid_volume = max(order_depth.buy_orders.items())
        self.add_sell_order(
            result=result,
            planned_position=planned_position,
            product=product,
            price=int(best_bid),
            quantity=min(abs(int(best_bid_volume)), max_take_size),
        )

    def add_buy_order(
        self,
        result: Dict[str, List[Order]],
        planned_position: Dict[str, int],
        product: str,
        price: int,
        quantity: int,
    ) -> None:
        limit = self.LIMITS.get(product, self.DEFAULT_LIMIT)
        current_planned = planned_position.get(product, 0)
        buy_capacity = limit - current_planned
        quantity = int(max(0, min(quantity, buy_capacity)))

        if quantity <= 0:
            return

        if product not in result:
            result[product] = []
        result[product].append(Order(product, int(price), quantity))
        planned_position[product] = current_planned + quantity

    def add_sell_order(
        self,
        result: Dict[str, List[Order]],
        planned_position: Dict[str, int],
        product: str,
        price: int,
        quantity: int,
    ) -> None:
        limit = self.LIMITS.get(product, self.DEFAULT_LIMIT)
        current_planned = planned_position.get(product, 0)
        sell_capacity = limit + current_planned
        quantity = int(max(0, min(quantity, sell_capacity)))

        if quantity <= 0:
            return

        if product not in result:
            result[product] = []
        result[product].append(Order(product, int(price), -quantity))
        planned_position[product] = current_planned - quantity

    # ------------------------------------------------------------------
    # Market data helpers
    # ------------------------------------------------------------------
    def get_mid_price(self, order_depth: OrderDepth) -> Optional[float]:
        if len(order_depth.buy_orders) == 0 or len(order_depth.sell_orders) == 0:
            return None
        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())
        return (float(best_bid) + float(best_ask)) / 2.0

    def safe_float(self, value: Any, fallback: float) -> float:
        try:
            return float(value)
        except Exception:
            return fallback
