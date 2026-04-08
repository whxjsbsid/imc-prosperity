from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import json


class Trader:

    def bid(self):
        return 15

    def run(self, state: TradingState):
        """Only method required. It takes all buy and sell orders for all
        symbols as an input, and outputs a list of orders to be sent."""

        print("traderData: " + state.traderData)
        print("Observations: " + str(state.observations))

        # Load previous tick data
        if state.traderData:
            prev_data = json.loads(state.traderData)
        else:
            prev_data = {}

        # Store current tick prices for next run
        new_data = {}

        # Orders to be placed on exchange matching engine
        result = {}

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            # Safely get best bid / best ask
            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

            # Current mid price
            current_mid = None
            if best_bid is not None and best_ask is not None:
                current_mid = (best_bid + best_ask) / 2
            elif best_bid is not None:
                current_mid = best_bid
            elif best_ask is not None:
                current_mid = best_ask

            # Fair value logic
            if product == "EMERALDS":
                acceptable_price = 10000
            elif product == "TOMATOES":
                acceptable_price = prev_data.get("TOMATOES", current_mid)
            else:
                acceptable_price = current_mid

            print(f"{product} acceptable price: {acceptable_price}")
            print(
                f"Buy Order depth: {len(order_depth.buy_orders)}, "
                f"Sell order depth: {len(order_depth.sell_orders)}"
            )

            # Buy if best ask is below acceptable price
            if best_ask is not None:
                best_ask_amount = order_depth.sell_orders[best_ask]
                if best_ask < acceptable_price:
                    print("BUY", str(-best_ask_amount) + "x", best_ask)
                    orders.append(Order(product, best_ask, -best_ask_amount))

            # Sell if best bid is above acceptable price
            if best_bid is not None:
                best_bid_amount = order_depth.buy_orders[best_bid]
                if best_bid > acceptable_price:
                    print("SELL", str(best_bid_amount) + "x", best_bid)
                    orders.append(Order(product, best_bid, -best_bid_amount))

            result[product] = orders

            # Save current mid for next tick
            if current_mid is not None:
                new_data[product] = current_mid

        # Save data into traderData for next execution
        traderData = json.dumps(new_data)

        conversions = 0
        return result, conversions, traderData
