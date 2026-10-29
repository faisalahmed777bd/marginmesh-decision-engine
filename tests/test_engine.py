import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marginmesh_engine import Action, LotOffer, RouteEconomics, recommend_action


def example_offer(**overrides):
    values = dict(
        lot_id="LOT-001",
        sku="Apex Model X 256GB",
        quantity=500,
        ask_price=410.0,
        home_route="UK",
        target_margin=0.05,
        reroute_advantage=0.01,
        supplier_risk_cost=0.0,
        routes=(
            RouteEconomics(
                name="UK",
                resale_prices={0: 438, 14: 432, 30: 424, 60: 405},
                freight=8,
                warranty_returns=5,
                financing=3,
                route_risk_cost=10,
            ),
            RouteEconomics(
                name="UAE",
                resale_prices={0: 456, 14: 448, 30: 438, 60: 416},
                freight=8,
                warranty_returns=5,
                financing=3,
                route_risk_cost=10,
            ),
            RouteEconomics(
                name="EU",
                resale_prices={0: 448, 14: 442, 30: 432, 60: 411},
                freight=8,
                warranty_returns=5,
                financing=3,
                route_risk_cost=12,
            ),
        ),
    )
    values.update(overrides)
    return LotOffer(**values)


class RecommendationTests(unittest.TestCase):
    def test_recommends_negotiation_when_no_route_meets_floor_at_ask(self):
        result = recommend_action(example_offer())
        self.assertEqual(result.action, Action.NEGOTIATE)
        self.assertEqual(result.recommended_route, "UAE")
        self.assertAlmostEqual(result.maximum_acceptable_buy_price, 407.2, places=2)
        self.assertIn("No route clears", result.explanation)

    def test_lower_ask_turns_negotiation_into_reroute(self):
        result = recommend_action(example_offer(ask_price=388))
        self.assertEqual(result.action, Action.REROUTE)
        self.assertEqual(result.recommended_route, "UAE")
        selected = next(p for p in result.projections if p.route == "UAE" and p.day == 0)
        self.assertAlmostEqual(selected.margin, 0.0921, places=3)

    def test_reroute_only_when_route_advantage_clears_threshold(self):
        routes = (
            RouteEconomics("UK", {0: 500, 14: 495, 30: 490, 60: 480}),
            RouteEconomics("EU", {0: 502, 14: 497, 30: 492, 60: 482}),
        )
        offer = example_offer(ask_price=400, routes=routes, home_route="UK", reroute_advantage=0.01)
        result = recommend_action(offer)
        self.assertEqual(result.action, Action.BUY)
        self.assertEqual(result.recommended_route, "UK")

    def test_rejects_when_no_positive_negotiation_ceiling(self):
        routes = (RouteEconomics("UK", {0: 100, 14: 90, 30: 80, 60: 70}, freight=100),)
        result = recommend_action(example_offer(ask_price=200, routes=routes, home_route="UK"))
        self.assertEqual(result.action, Action.REJECT)
        self.assertIsNone(result.maximum_acceptable_buy_price)
        self.assertIsNone(result.recommended_route)

    def test_projections_are_created_for_every_route_and_horizon(self):
        result = recommend_action(example_offer())
        self.assertEqual(len(result.projections), 12)
        self.assertEqual({p.day for p in result.projections}, {0, 14, 30, 60})

    def test_total_profit_scales_by_lot_quantity(self):
        result = recommend_action(example_offer(ask_price=388))
        projection = next(p for p in result.projections if p.route == "UAE" and p.day == 0)
        self.assertAlmostEqual(projection.net_profit_total, projection.net_profit_per_unit * 500)


class InputValidationTests(unittest.TestCase):
    def test_requires_all_forecast_horizons(self):
        with self.assertRaisesRegex(ValueError, "missing horizons"):
            RouteEconomics("UK", {0: 100, 14: 98, 30: 95})

    def test_rejects_negative_costs(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            RouteEconomics("UK", {0: 100, 14: 98, 30: 95, 60: 90}, freight=-1)

    def test_home_route_must_exist(self):
        with self.assertRaisesRegex(ValueError, "home_route"):
            example_offer(home_route="Mars")

    def test_margin_floor_must_be_valid(self):
        with self.assertRaisesRegex(ValueError, "target_margin"):
            example_offer(target_margin=1)


if __name__ == "__main__":
    unittest.main()
