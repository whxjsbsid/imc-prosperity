import json
from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Any, Optional, Tuple


class Trader:
    LIMITS = {
        "HYDROGEL_PACK": 200,
        "VELVETFRUIT_EXTRACT": 200,
    }

    # HYDROGEL_PACK parameters
    # Same as previous safer setup: fixed fair + extreme-only taking.
    HYDROGEL_FAIR = 9990.8
    HYDROGEL_EDGE = 31.94 * 0.9
    HYDROGEL_MAX_TAKE_SIZE = 100

    # VELVETFRUIT_EXTRACT parameters
    # Base logic remains fixed fair + extreme-only taking.
    VELVETFRUIT_FAIR = 5250.1
    VELVETFRUIT_EDGE = 15.63 * 1.4
    VELVETFRUIT_MAX_TAKE_SIZE = 100

    # Counterparty signal
    # Round 4 exposes buyer/seller IDs. Historical scan suggests Mark 67 is the
    # most useful VELVETFRUIT_EXTRACT directional counterparty.
    INFORMED_TRADER = "Mark 67"

    # Keep this signal short-lived because we only see market_trades after they
    # happened. It should bias us, not completely override the main strategy.
    MARK67_DECAY = 0.70
    MARK67_SIGNAL_CAP = 40.0
    MARK67_SIGNAL_TO_FAIR_BIAS = 0.20
    MARK67_MAX_FAIR_BIAS = 4.0

    # If Mark 67 was recently buying, avoid selling too easily against him.
    # This does not ban sells; it just requires a more expensive bid before we sell.
    MARK67_SELL_BLOCK_SIGNAL = 5.0
    MARK67_SELL_BLOCK_EXTRA_EDGE = 5.0

    # Small optional follow trade when Mark 67's signal is fresh and the ask is
    # still close to fair. This is intentionally small because the signal is lagged.
    MARK67_EXTRA_BUY_SIGNAL = 8.0
    MARK67_EXTRA_BUY_SIZE = 100
    MARK67_MAX_CHASE_ABOVE_FAIR = 3.0

    def bid(self):
        return 3000

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        data = self.decode_trader_data(state.traderData)
        mark67_signal = self.update_mark67_velvet_signal(state, data)

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
                mark67_signal,
            )

        traderData = self.encode_trader_data({
            "mark67_velvet_signal": mark67_signal,
        })
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
    # Counterparty signal
    # ------------------------------------------------------------------
    def update_mark67_velvet_signal(
        self,
        state: TradingState,
        data: Dict[str, Any],
    ) -> float:
        """
        Positive signal = Mark 67 recently bought VELVETFRUIT_EXTRACT.
        Negative signal = Mark 67 recently sold VELVETFRUIT_EXTRACT.

        In the historical Round 4 data, Mark 67 mostly appears as a buyer, so
        the practical effect is mainly: buy slightly more / avoid shorting too
        easily after Mark 67 buys.
        """
        old_signal = float(data.get("mark67_velvet_signal", 0.0))
        signal = old_signal * self.MARK67_DECAY

        for trade in state.market_trades.get("VELVETFRUIT_EXTRACT", []):
            buyer = trade.buyer or ""
            seller = trade.seller or ""
            qty = abs(int(trade.quantity))

            if buyer == self.INFORMED_TRADER:
                signal += qty
            if seller == self.INFORMED_TRADER:
                signal -= qty

        if signal > self.MARK67_SIGNAL_CAP:
            signal = self.MARK67_SIGNAL_CAP
        elif signal < -self.MARK67_SIGNAL_CAP:
            signal = -self.MARK67_SIGNAL_CAP

        return signal

    # ------------------------------------------------------------------
    # Product logic
    # ------------------------------------------------------------------
    def trade_hydrogel(
        self,
        state: TradingState,
        product: str,
        order_depth: OrderDepth,
    ) -> List[Order]:
        """
        HYDROGEL logic:
        1. Use a fixed fair value around the historical average.
        2. Buy only extreme cheap asks below fair - edge.
        3. Sell only extreme expensive bids above fair + edge.
        4. No counterparty overlay for Hydrogel.
        """
        return self.trade_extreme_mean_reversion(
            state=state,
            product=product,
            order_depth=order_depth,
            fair_value=self.HYDROGEL_FAIR,
            edge=self.HYDROGEL_EDGE,
            max_take_size=self.HYDROGEL_MAX_TAKE_SIZE,
        )

    def trade_velvetfruit(
        self,
        state: TradingState,
        product: str,
        order_depth: OrderDepth,
        mark67_signal: float,
    ) -> List[Order]:
        """
        VELVETFRUIT logic:
        1. Start with the old fixed-fair extreme-only strategy.
        2. Adjust fair value slightly using Mark 67's recent flow.
        3. If Mark 67 recently bought, require a higher bid before selling.
        4. If Mark 67 recently bought and best ask is still close to fair, take
           a small extra buy.
        """
        fair_bias = self.clip(
            mark67_signal * self.MARK67_SIGNAL_TO_FAIR_BIAS,
            -self.MARK67_MAX_FAIR_BIAS,
            self.MARK67_MAX_FAIR_BIAS,
        )

        adjusted_fair = self.VELVETFRUIT_FAIR + fair_bias
        sell_extra_edge = 0.0
        extra_buy_threshold: Optional[float] = None
        extra_buy_size = 0

        if mark67_signal >= self.MARK67_SELL_BLOCK_SIGNAL:
            sell_extra_edge = self.MARK67_SELL_BLOCK_EXTRA_EDGE

        if mark67_signal >= self.MARK67_EXTRA_BUY_SIGNAL:
            extra_buy_threshold = self.VELVETFRUIT_FAIR + self.MARK67_MAX_CHASE_ABOVE_FAIR
            extra_buy_size = self.MARK67_EXTRA_BUY_SIZE

        return self.trade_extreme_mean_reversion(
            state=state,
            product=product,
            order_depth=order_depth,
            fair_value=adjusted_fair,
            edge=self.VELVETFRUIT_EDGE,
            max_take_size=self.VELVETFRUIT_MAX_TAKE_SIZE,
            sell_extra_edge=sell_extra_edge,
            extra_buy_threshold=extra_buy_threshold,
            extra_buy_size=extra_buy_size,
        )

    # ------------------------------------------------------------------
    # Shared execution logic
    # ------------------------------------------------------------------
    def trade_extreme_mean_reversion(
        self,
        state: TradingState,
        product: str,
        order_depth: OrderDepth,
        fair_value: float,
        edge: float,
        max_take_size: int,
        sell_extra_edge: float = 0.0,
        extra_buy_threshold: Optional[float] = None,
        extra_buy_size: int = 0,
    ) -> List[Order]:
        """
        Shared extreme-only mean reversion logic.

        Main trades:
        - Buy asks below fair - edge.
        - Sell bids above fair + edge.

        Optional Mark 67 overlay for Velvetfruit:
        - small extra buy if best ask is still near fair after Mark 67 buying;
        - require a higher sell threshold after Mark 67 buying.
        """
        orders: List[Order] = []

        limit = self.LIMITS[product]
        buy_threshold = fair_value - edge
        sell_threshold = fair_value + edge + sell_extra_edge
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

        # 1) Take clearly cheap asks.
        bought_from_extreme = False
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if buy_capacity <= 0:
                break
            if ask_price < buy_threshold:
                buy_qty = min(-ask_volume, max_take_size)
                before = buy_capacity
                add_buy(ask_price, buy_qty)
                if buy_capacity < before:
                    bought_from_extreme = True
            else:
                break

        # 2) Small Mark 67 follow-buy, but only if we did not already buy from
        # an extreme ask and the best ask is still close to fair.
        if (
            extra_buy_threshold is not None
            and extra_buy_size > 0
            and not bought_from_extreme
            and buy_capacity > 0
            and len(order_depth.sell_orders) > 0
        ):
            best_ask, best_ask_volume = min(order_depth.sell_orders.items())
            if best_ask <= extra_buy_threshold:
                add_buy(best_ask, min(-best_ask_volume, extra_buy_size))

        # 3) Hit clearly expensive bids.
        for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if sell_capacity <= 0:
                break
            if bid_price > sell_threshold:
                sell_qty = min(bid_volume, max_take_size)
                add_sell(bid_price, sell_qty)
            else:
                break

        return orders

    def clip(self, x: float, lo: float, hi: float) -> float:
        if x < lo:
            return lo
        if x > hi:
            return hi
        return x
