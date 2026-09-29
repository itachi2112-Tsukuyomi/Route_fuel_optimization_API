"""Where to buy fuel, and how much, along a fixed route.

Two solvers over the same inputs (stations sorted by mile marker):

`plan_fuel_stops` (used by the API) is a dynamic program over
(station, fuel in tank). It minimises

        fuel cost  +  stop_penalty x number_of_stops

so the plan doesn't pull off the interstate to buy 1 gallon and save 3
cents. Fuel is tracked in steps of `FUEL_STEP_MILES` of range (0.1 gallon at
10 mpg). Buying at a station is a "prefix minimum" over arrival fuel levels,
so each station costs O(tank levels) numpy work: about 1 ms per 100 stations.

`plan_fuel_stops_greedy` is the classic exact greedy for the pure-cost
problem (no stop penalty): at each station, if a cheaper station is within a
full tank, buy just enough to reach it; otherwise fill up. With
stop_penalty=0 the DP matches it, which the tests check.

Everything works in miles of range; gallons = miles / mpg.
"""

from dataclasses import dataclass

import numpy as np

EPS = 1e-9
FUEL_STEP_MILES = 1.0


class NoFeasiblePlan(Exception):
    """Some gap between fuel stations is longer than the vehicle's range."""

    def __init__(self, message, from_mile, to_mile):
        super().__init__(message)
        self.from_mile = from_mile
        self.to_mile = to_mile


@dataclass
class Purchase:
    position: int  # index into the input station list
    mile: float
    price: float
    gallons: float
    fuel_before_gallons: float  # in the tank on arrival

    @property
    def cost(self):
        return self.gallons * self.price


@dataclass
class FuelPlan:
    purchases: list
    total_gallons: float
    total_cost: float
    fuel_left_gallons: float


def _validate(miles, prices):
    if len(miles) != len(prices):
        raise ValueError("miles and prices must have the same length")
    if any(b < a for a, b in zip(miles, miles[1:])):
        raise ValueError("stations must be sorted by mile")


def _check_gaps(miles, total_miles, max_range, start_fuel_miles):
    """Raise NoFeasiblePlan for the first stretch the vehicle can't cover."""
    reach, last = min(start_fuel_miles, max_range), 0.0
    for mile in [*miles, total_miles]:
        if mile - last > reach + EPS:
            where = "the destination" if mile == total_miles else f"mile {mile:.0f}"
            raise NoFeasiblePlan(
                f"No fuel station within range between mile {last:.0f} and {where} "
                f"({mile - last:.0f} miles; range is {max_range:.0f}).",
                last, mile,
            )
        reach, last = max_range, mile


def _plan(purchases, total_miles, mpg, fuel_left_miles):
    return FuelPlan(
        purchases=purchases,
        total_gallons=sum(p.gallons for p in purchases),
        total_cost=sum(p.cost for p in purchases),
        fuel_left_gallons=max(fuel_left_miles, 0.0) / mpg,
    )


def plan_fuel_stops(
    miles, prices, total_miles, max_range, mpg, start_fuel_miles, stop_penalty=0.0
):
    """Cheapest plan (fuel cost + stop_penalty per stop).

    miles:  station positions along the route, ascending, within 0..total_miles
    prices: price per gallon at each station
    start_fuel_miles: range already in the tank at the start
    """
    _validate(miles, prices)
    keep = [i for i, m in enumerate(miles) if m <= total_miles]
    miles = [miles[i] for i in keep]
    prices = [prices[i] for i in keep]
    _check_gaps(miles, total_miles, max_range, start_fuel_miles)

    step = FUEL_STEP_MILES
    levels = int(round(max_range / step))
    grid = np.arange(levels + 1)
    inf = np.inf

    # cost[f]: cheapest spend so far, standing at the current point with f fuel steps.
    cost = np.full(levels + 1, inf)
    cost[min(int(round(start_fuel_miles / step)), levels)] = 0.0
    here = 0  # current position, in steps
    history = []  # per station: (arrival position, bought[g], arrival_level[g])

    for mile, price in zip(miles, prices):
        pos = int(round(mile / step))
        cost = _drive(cost, pos - here)
        here = pos

        per_step = price * step / mpg
        # Buying up to level g from some arrival level f < g costs
        #   cost[f] + (g - f) * per_step + stop_penalty
        # = (g * per_step + stop_penalty) + min_{f<g} (cost[f] - f * per_step)
        shifted = cost - grid * per_step
        run_min = np.minimum.accumulate(shifted)
        is_min = shifted <= run_min
        run_arg = np.maximum.accumulate(np.where(is_min, grid, 0))
        best_prev = np.concatenate(([inf], run_min[:-1]))
        best_arg = np.concatenate(([0], run_arg[:-1]))
        buy_cost = best_prev + grid * per_step + stop_penalty

        bought = buy_cost < cost - EPS
        history.append((pos, bought, best_arg))
        cost = np.where(bought, buy_cost, cost)

    end = int(round(total_miles / step))
    cost = _drive(cost, end - here)
    if not np.isfinite(cost).any():  # only possible through rounding at the edges
        raise NoFeasiblePlan("No feasible fuel plan for this route.", 0.0, total_miles)
    level = int(np.argmin(cost))  # ties -> least fuel wasted at the destination

    # Walk back through the stations to recover where we stop and the fuel
    # level we leave each stop with.
    stops = {}
    after = end
    for i in range(len(history) - 1, -1, -1):
        pos, bought, best_arg = history[i]
        level += after - pos  # fuel on leaving station i
        if bought[level]:
            stops[i] = level * step
            level = int(best_arg[level])
        after = pos

    return _replay(stops, miles, prices, total_miles, max_range, mpg, start_fuel_miles)


def _replay(stops, miles, prices, total_miles, max_range, mpg, start_fuel_miles):
    """Turn {station: fuel level on leaving} into exact purchases.

    The DP rounds positions to whole fuel steps; replaying with the real
    distances keeps the tank within capacity and always reaches the next stop.
    """
    order = sorted(stops)
    fuel, position = min(start_fuel_miles, max_range), 0.0
    purchases = []
    for n, i in enumerate(order):
        fuel -= miles[i] - position
        position = miles[i]
        next_mile = miles[order[n + 1]] if n + 1 < len(order) else total_miles
        target = min(max_range, max(stops[i], next_mile - position))
        if target - fuel > EPS:
            purchases.append(
                Purchase(i, miles[i], prices[i], (target - fuel) / mpg, max(fuel, 0.0) / mpg)
            )
            fuel = target
    return _plan(purchases, total_miles, mpg, fuel - (total_miles - position))


def _drive(cost, steps):
    """Fuel levels after driving `steps`: level f becomes f - steps."""
    if steps <= 0:
        return cost
    if steps >= len(cost):
        return np.full(len(cost), np.inf)
    return np.concatenate((cost[steps:], np.full(steps, np.inf)))


def _next_cheaper(prices):
    """Index of the first strictly cheaper station ahead (monotonic stack)."""
    result = [None] * len(prices)
    stack = []
    for i, price in enumerate(prices):
        while stack and prices[stack[-1]] > price:
            result[stack.pop()] = i
        stack.append(i)
    return result


def plan_fuel_stops_greedy(miles, prices, total_miles, max_range, mpg, start_fuel_miles):
    """Exact minimum fuel cost, ignoring the number of stops."""
    _validate(miles, prices)
    _check_gaps([m for m in miles if m <= total_miles], total_miles, max_range,
                start_fuel_miles)
    nxt = _next_cheaper(prices)
    fuel = min(start_fuel_miles, max_range)
    position = 0.0
    purchases = []

    for i, (mile, price) in enumerate(zip(miles, prices)):
        if mile > total_miles:
            break
        fuel -= mile - position
        position = mile
        to_finish = total_miles - mile
        j = nxt[i]
        if j is not None and miles[j] - mile <= max_range:
            target = min(miles[j] - mile, to_finish)  # just enough for the cheaper stop
        else:
            target = min(max_range, to_finish)  # fill up, or just enough to finish
        buy = target - fuel
        if buy > EPS:
            purchases.append(Purchase(i, mile, price, buy / mpg, max(fuel, 0.0) / mpg))
            fuel = target

    return _plan(purchases, total_miles, mpg, fuel - (total_miles - position))
