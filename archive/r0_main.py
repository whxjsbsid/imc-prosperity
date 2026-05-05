from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List, Dict
import json


class Trader:
    POSITION_LIMITS = {
        "EMERALDS": 80,   
        "TOMATOES": 80,  
    }

    EMERALDS_FAIR_VALUE = 10000
    EMERALDS_MM_SIZE = 5
    TOMATOES_MA_WINDOW = 20

    def bid(self):
        return 15

    def run(self, state: TradingState):
        print("traderData: " + state.traderData)
        print("Observations: " + str(state.observations))

        if state.traderData:
            try:
                prev_data = json.loads(state.traderData)
            except json.JSONDecodeError:
                prev_data = {}
        else:
            prev_data = {}

        price_history = prev_data.get("price_history", {})

        if "TOMATOES" not in price_history:
            old_tomato = prev_data.get("TOMATOES")
            if isinstance(old_tomato, (int, float)):
                price_history["TOMATOES"] = [old_tomato]
            else:
                price_history["TOMATOES"] = []

        result: Dict[str, List[Order]] = {}

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            position = state.position.get(product, 0)
            limit = self.POSITION_LIMITS.get(product, 20)

            buy_capacity = limit - position
            sell_capacity = limit + position

            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

            current_mid = None
            if best_bid is not None and best_ask is not None:
                current_mid = (best_bid + best_ask) / 2
            elif best_bid is not None:
                current_mid = best_bid
            elif best_ask is not None:
                current_mid = best_ask
                

            # EMERALDS: fixed fair value + market making
            if product == "EMERALDS":
                acceptable_price = 10000
                position = state.position.get(product, 0)
                limit = self.POSITION_LIMITS[product]
            
                buy_capacity = limit - position
                sell_capacity = limit + position
            
                print(f"{product} acceptable price: {acceptable_price}")
                print(f"Position: {position}, Buy cap: {buy_capacity}, Sell cap: {sell_capacity}")
            
                for ask_price in sorted(order_depth.sell_orders.keys()):
                    if buy_capacity <= 0:
                        break
            
                    ask_volume = -order_depth.sell_orders[ask_price]
                    if ask_price < acceptable_price:
                        qty = min(ask_volume, buy_capacity)
                        if qty > 0:
                            print("TAKE BUY", f"{qty}x", ask_price)
                            orders.append(Order(product, ask_price, qty))
                            buy_capacity -= qty
                    else:
                        break
            
                for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                    if sell_capacity <= 0:
                        break
            
                    bid_volume = order_depth.buy_orders[bid_price]
                    if bid_price > acceptable_price:
                        qty = min(bid_volume, sell_capacity)
                        if qty > 0:
                            print("TAKE SELL", f"{qty}x", bid_price)
                            orders.append(Order(product, bid_price, -qty))
                            sell_capacity -= qty
                    else:
                        break
            
                if (
                    best_bid is not None
                    and best_ask is not None
                    and best_bid < acceptable_price < best_ask
                ):
                    buy_quote = best_bid + 1
                    sell_quote = best_ask - 1
            
                    if buy_quote < sell_quote:
                        mm_size = 5
            
                        if buy_capacity > 0:
                            qty = min(mm_size, buy_capacity)
                            print("MM BUY", f"{qty}x", buy_quote)
                            orders.append(Order(product, buy_quote, qty))
            
                        if sell_capacity > 0:
                            qty = min(mm_size, sell_capacity)
                            print("MM SELL", f"{qty}x", sell_quote)
                            orders.append(Order(product, sell_quote, -qty))

            
            # TOMATOES: 10-tick moving average
            elif product == "TOMATOES":
                tomato_history = price_history.get("TOMATOES", [])

                if current_mid is not None:
                    tomato_history.append(current_mid)
                    tomato_history = tomato_history[-self.TOMATOES_MA_WINDOW:]

                price_history["TOMATOES"] = tomato_history

                if tomato_history:
                    acceptable_price = sum(tomato_history) / len(tomato_history)
                else:
                    acceptable_price = current_mid

                print(f"{product} acceptable price (10-tick MA): {acceptable_price}")
                print(
                    f"Position: {position}, Buy cap: {buy_capacity}, Sell cap: {sell_capacity}"
                )

                if acceptable_price is not None:
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if buy_capacity <= 0:
                            break

                        ask_volume = -order_depth.sell_orders[ask_price]
                        if ask_price < acceptable_price:
                            qty = min(ask_volume, buy_capacity)
                            if qty > 0:
                                print("BUY", f"{qty}x", ask_price)
                                orders.append(Order(product, ask_price, qty))
                                buy_capacity -= qty
                        else:
                            break

                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if sell_capacity <= 0:
                            break

                        bid_volume = order_depth.buy_orders[bid_price]
                        if bid_price > acceptable_price:
                            qty = min(bid_volume, sell_capacity)
                            if qty > 0:
                                print("SELL", f"{qty}x", bid_price)
                                orders.append(Order(product, bid_price, -qty))
                                sell_capacity -= qty
                        else:
                            break
                            
            else:
                print(f"{product}: no strategy")
                pass

            result[product] = orders

        # Save updated traderData
        traderData = json.dumps({
            "price_history": price_history
        })

        conversions = 0
        return result, conversions, traderData
