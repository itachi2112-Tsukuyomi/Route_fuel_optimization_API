import random

from django.test import SimpleTestCase

from fuel.services.optimizer import (
    NoFeasiblePlan,
    plan_fuel_stops,
    plan_fuel_stops_greedy,
)

RANGE, MPG = 500.0, 10.0


def assert_plan_is_drivable(test, plan, miles, total, start_fuel):
    """Replay the plan: the tank never runs dry and never overflows."""
    bought = {p.position: p.gallons * MPG for p in plan.purchases}
    fuel, pos = start_fuel, 0.0
    for i, mile in enumerate(miles):
        fuel -= mile - pos
        pos = mile
        test.assertGreaterEqual(fuel, -1.0, f"ran dry before station {i}")
        fuel += bought.get(i, 0.0)
        test.assertLessEqual(fuel, RANGE + 1e-6, f"overfilled at station {i}")
    test.assertGreaterEqual(fuel - (total - pos), -1.0, "ran dry before destination")


class GreedyTests(SimpleTestCase):
    def test_no_stops_needed_when_trip_fits_in_tank(self):
        plan = plan_fuel_stops_greedy([100, 200], [3.0, 2.0], 400, RANGE, MPG, RANGE)
        self.assertEqual(plan.purchases, [])
        self.assertEqual(plan.total_cost, 0)

    def test_buys_just_enough_to_reach_cheaper_station(self):
        # Start empty at a $4 station; a $2 station is 100 miles ahead.
        plan = plan_fuel_stops_greedy([0, 100], [4.0, 2.0], 600, RANGE, MPG, 0)
        (a, b) = plan.purchases
        self.assertAlmostEqual(a.gallons, 10)  # 100 miles at $4
        self.assertAlmostEqual(b.gallons, 50)  # remaining 500 miles at $2
        self.assertAlmostEqual(plan.total_cost, 10 * 4 + 50 * 2)

    def test_fills_up_when_no_cheaper_station_in_range(self):
        # Cheap at mile 0, expensive later: fill up at the cheap one.
        plan = plan_fuel_stops_greedy([0, 400], [2.0, 5.0], 800, RANGE, MPG, 0)
        self.assertAlmostEqual(plan.purchases[0].gallons, 50)
        self.assertAlmostEqual(plan.purchases[1].gallons, 30)
        self.assertAlmostEqual(plan.total_cost, 50 * 2 + 30 * 5)


class DynamicProgrammingTests(SimpleTestCase):
    def test_matches_greedy_without_stop_penalty(self):
        rng = random.Random(42)
        for _ in range(200):
            total = rng.uniform(300, 2500)
            miles = sorted(rng.uniform(0, total) for _ in range(rng.randint(5, 60)))
            prices = [round(rng.uniform(2.5, 5.0), 3) for _ in miles]
            start = rng.choice([RANGE, miles[0]])
            try:
                greedy = plan_fuel_stops_greedy(miles, prices, total, RANGE, MPG, start)
            except NoFeasiblePlan:
                with self.assertRaises(NoFeasiblePlan):
                    plan_fuel_stops(miles, prices, total, RANGE, MPG, start)
                continue
            dp = plan_fuel_stops(miles, prices, total, RANGE, MPG, start)
            # The DP rounds positions to whole miles (0.1 gal), worth at most
            # ~$0.25 per stop at these prices.
            tolerance = 0.25 * (len(greedy.purchases) + 1)
            self.assertAlmostEqual(dp.total_cost, greedy.total_cost, delta=tolerance)
            self.assertLessEqual(len(dp.purchases), len(greedy.purchases) + 1)
            assert_plan_is_drivable(self, dp, miles, total, start)

    def test_stop_penalty_reduces_stops_and_stays_drivable(self):
        rng = random.Random(7)
        miles = sorted(rng.uniform(0, 2000) for _ in range(150))
        prices = [round(rng.uniform(2.8, 4.0), 3) for _ in miles]
        cheapest = plan_fuel_stops(miles, prices, 2000, RANGE, MPG, RANGE, stop_penalty=0)
        fewer = plan_fuel_stops(miles, prices, 2000, RANGE, MPG, RANGE, stop_penalty=25)
        self.assertLess(len(fewer.purchases), len(cheapest.purchases))
        self.assertGreaterEqual(fewer.total_cost, cheapest.total_cost - 0.01)
        assert_plan_is_drivable(self, fewer, miles, 2000, RANGE)

    def test_total_gallons_cover_the_trip(self):
        miles = [0, 150, 420, 700, 950, 1200]
        prices = [3.5, 3.1, 3.9, 2.9, 3.3, 3.0]
        plan = plan_fuel_stops(miles, prices, 1400, RANGE, MPG, 0, stop_penalty=5)
        self.assertAlmostEqual(plan.total_gallons - plan.fuel_left_gallons, 140, delta=0.2)

    def test_gap_longer_than_range_is_reported(self):
        with self.assertRaises(NoFeasiblePlan) as ctx:
            plan_fuel_stops([100, 700], [3.0, 3.0], 900, RANGE, MPG, RANGE)
        self.assertEqual((ctx.exception.from_mile, ctx.exception.to_mile), (100, 700))

    def test_unreachable_first_station_is_reported(self):
        with self.assertRaises(NoFeasiblePlan):
            plan_fuel_stops([300], [3.0], 600, RANGE, MPG, start_fuel_miles=200)

    def test_stations_past_destination_are_ignored(self):
        plan = plan_fuel_stops([100, 900], [3.0, 1.0], 550, RANGE, MPG, RANGE)
        self.assertTrue(all(p.mile <= 550 for p in plan.purchases))
