"""Transparent lot-level decision logic for a wholesale electronics offer.

This module deliberately uses explicit inputs and deterministic rules. It is a
small, auditable decision core, not a trained forecasting model or a trading
system. Callers provide resale assumptions and per-unit cost estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Mapping


HORIZONS = (0, 14, 30, 60)


class Action(str, Enum):
    """Commercial action selected for the offer."""

    BUY = "BUY"
    NEGOTIATE = "NEGOTIATE"
    REROUTE = "REROUTE"
    REJECT = "REJECT"


def _require_finite_nonnegative(name: str, value: float) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite, non-negative number")


@dataclass(frozen=True)
class RouteEconomics:
    """Per-unit resale and route costs for one destination market.

    ``resale_prices`` must include estimates for day 0, 14, 30, and 60. The
    remaining fields are per-unit costs in the same currency as the offer.
    ``route_risk_cost`` is a user-supplied monetary risk adjustment, not a
    probability or a model output.
    """

    name: str
    resale_prices: Mapping[int, float]
    freight: float = 0.0
    duties: float = 0.0
    handling: float = 0.0
    financing: float = 0.0
    warranty_returns: float = 0.0
    route_risk_cost: float = 0.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("route name cannot be empty")
        missing = set(HORIZONS) - set(self.resale_prices)
        if missing:
            raise ValueError(f"{self.name}: resale_prices missing horizons {sorted(missing)}")
        for day in HORIZONS:
            _require_finite_nonnegative(f"{self.name}.resale_prices[{day}]", self.resale_prices[day])
        for field_name in (
            "freight", "duties", "handling", "financing", "warranty_returns", "route_risk_cost"
        ):
            _require_finite_nonnegative(field_name, getattr(self, field_name))

    @property
    def non_purchase_cost(self) -> float:
        """All estimated per-unit costs excluding supplier purchase price."""

        return (
            self.freight
            + self.duties
            + self.handling
            + self.financing
            + self.warranty_returns
            + self.route_risk_cost
        )


@dataclass(frozen=True)
class LotOffer:
    """A proposed stock lot and the commercial constraints for evaluating it."""

    lot_id: str
    sku: str
    quantity: int
    ask_price: float
    home_route: str
    routes: tuple[RouteEconomics, ...]
    target_margin: float = 0.05
    reroute_advantage: float = 0.01
    supplier_risk_cost: float = 0.0

    def __post_init__(self) -> None:
        if not self.lot_id.strip() or not self.sku.strip():
            raise ValueError("lot_id and sku cannot be empty")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        _require_finite_nonnegative("ask_price", self.ask_price)
        _require_finite_nonnegative("supplier_risk_cost", self.supplier_risk_cost)
        if not 0 <= self.target_margin < 1:
            raise ValueError("target_margin must be in the range [0, 1)")
        if not 0 <= self.reroute_advantage < 1:
            raise ValueError("reroute_advantage must be in the range [0, 1)")
        if not self.routes:
            raise ValueError("at least one route is required")
        names = [route.name for route in self.routes]
        if len(names) != len(set(names)):
            raise ValueError("route names must be unique")
        if self.home_route not in names:
            raise ValueError("home_route must match one of the supplied route names")


@dataclass(frozen=True)
class RouteProjection:
    """Route economics at the ask price across the four forecast horizons."""

    route: str
    day: int
    resale_price: float
    adjusted_cost: float
    net_profit_per_unit: float
    net_profit_total: float
    margin: float


@dataclass(frozen=True)
class ActionCandidate:
    """A ranked action with a visible calculation trail."""

    action: Action
    route: str | None
    score: int
    expected_margin: float | None
    expected_profit_per_unit: float | None
    buy_price: float | None
    rationale: str


@dataclass(frozen=True)
class Decision:
    """Recommendation plus the route, negotiation threshold, and projections."""

    lot_id: str
    sku: str
    action: Action
    recommended_route: str | None
    ask_price: float
    maximum_acceptable_buy_price: float | None
    target_margin: float
    explanation: str
    projections: tuple[RouteProjection, ...]
    candidates: tuple[ActionCandidate, ...]


def _projection(offer: LotOffer, route: RouteEconomics, day: int) -> RouteProjection:
    resale = route.resale_prices[day]
    total_cost = offer.ask_price + offer.supplier_risk_cost + route.non_purchase_cost
    profit = resale - total_cost
    margin = profit / resale if resale else -1.0
    return RouteProjection(
        route=route.name,
        day=day,
        resale_price=resale,
        adjusted_cost=total_cost,
        net_profit_per_unit=profit,
        net_profit_total=profit * offer.quantity,
        margin=margin,
    )


def _score(margin: float, target_margin: float) -> int:
    """Map margin to a 0-100 index: 50 at target, +10 pp reaches 100."""

    return max(0, min(100, round(50 + (margin - target_margin) * 500)))


def _max_buy_price(offer: LotOffer, route: RouteEconomics) -> float:
    """Purchase ceiling that exactly preserves the target margin today."""

    required_resale = route.resale_prices[0] * (1 - offer.target_margin)
    return max(0.0, required_resale - offer.supplier_risk_cost - route.non_purchase_cost)


def recommend_action(offer: LotOffer) -> Decision:
    """Evaluate route economics and return an explainable commercial action.

    The engine selects actions using day-0 estimated unit profit subject to the
    customer margin floor. If no route clears the floor at the supplier ask, it
    returns NEGOTIATE when a positive purchase ceiling exists below the ask;
    otherwise it returns REJECT. A route change is recommended only when the
    selected route beats the home route by at least ``reroute_advantage`` in
    margin. All projected margins remain visible so users can challenge inputs.
    """

    by_name = {route.name: route for route in offer.routes}
    projections = tuple(
        _projection(offer, route, day)
        for route in offer.routes
        for day in HORIZONS
    )
    today = {route.name: _projection(offer, route, 0) for route in offer.routes}
    home = today[offer.home_route]

    viable = [projection for projection in today.values() if projection.margin >= offer.target_margin]
    candidates: list[ActionCandidate] = []

    if viable:
        best_viable = max(viable, key=lambda item: (item.net_profit_per_unit, item.margin))
        home_clears_floor = home.margin >= offer.target_margin
        route_change_is_material = (
            best_viable.route != offer.home_route
            and best_viable.margin - home.margin >= offer.reroute_advantage
        )

        if route_change_is_material or not home_clears_floor:
            action = Action.REROUTE
            chosen = best_viable
            rationale = (
                f"{chosen.route} is the strongest route that clears the "
                f"{offer.target_margin:.1%} margin floor, returning "
                f"{chosen.net_profit_per_unit:.2f} per unit."
            )
        else:
            action = Action.BUY
            chosen = home
            rationale = (
                f"The home route clears the {offer.target_margin:.1%} margin floor "
                f"at the supplier ask, returning {chosen.net_profit_per_unit:.2f} per unit."
            )

        candidates.append(ActionCandidate(
            action=action,
            route=chosen.route,
            score=_score(chosen.margin, offer.target_margin),
            expected_margin=chosen.margin,
            expected_profit_per_unit=chosen.net_profit_per_unit,
            buy_price=offer.ask_price,
            rationale=rationale,
        ))
        # Preserve the other viable route as a concrete alternative.
        alternatives = sorted(
            (item for item in viable if item.route != chosen.route),
            key=lambda item: item.net_profit_per_unit,
            reverse=True,
        )
        for alt in alternatives:
            candidates.append(ActionCandidate(
                action=Action.REROUTE if alt.route != offer.home_route else Action.BUY,
                route=alt.route,
                score=_score(alt.margin, offer.target_margin),
                expected_margin=alt.margin,
                expected_profit_per_unit=alt.net_profit_per_unit,
                buy_price=offer.ask_price,
                rationale=(
                    f"Alternative: {alt.route} returns {alt.net_profit_per_unit:.2f} per unit "
                    f"at a {alt.margin:.1%} margin."
                ),
            ))
        recommended_route = chosen.route
        max_buy = _max_buy_price(offer, by_name[chosen.route])
        explanation = rationale
        primary_action = action
    else:
        ceilings = {route.name: _max_buy_price(offer, route) for route in offer.routes}
        negotiation_route = max(offer.routes, key=lambda route: ceilings[route.name])
        ceiling = ceilings[negotiation_route.name]
        if ceiling > 0 and ceiling < offer.ask_price:
            action = Action.NEGOTIATE
            margin_at_ceiling = offer.target_margin
            expected_profit = (
                negotiation_route.resale_prices[0] * margin_at_ceiling
            )
            explanation = (
                f"No route clears the {offer.target_margin:.1%} margin floor at the ask. "
                f"Negotiate to at most {ceiling:.2f} per unit on {negotiation_route.name}; "
                f"that price reaches the floor using the supplied day-0 assumptions."
            )
            candidates.append(ActionCandidate(
                action=action,
                route=negotiation_route.name,
                score=50,
                expected_margin=margin_at_ceiling,
                expected_profit_per_unit=expected_profit,
                buy_price=ceiling,
                rationale=explanation,
            ))
            recommended_route = negotiation_route.name
            max_buy = ceiling
            primary_action = action
        else:
            worst = max(today.values(), key=lambda item: item.net_profit_per_unit)
            action = Action.REJECT
            explanation = (
                f"No route clears the {offer.target_margin:.1%} margin floor at the ask, "
                "and the supplied cost assumptions do not leave a positive negotiation ceiling. "
                f"The least-negative option is {worst.route} at {worst.net_profit_per_unit:.2f} per unit."
            )
            candidates.append(ActionCandidate(
                action=action,
                route=worst.route,
                score=0,
                expected_margin=worst.margin,
                expected_profit_per_unit=worst.net_profit_per_unit,
                buy_price=offer.ask_price,
                rationale=explanation,
            ))
            recommended_route = None
            max_buy = None
            primary_action = action

    candidates.append(ActionCandidate(
        action=Action.REJECT,
        route=None,
        score=0,
        expected_margin=None,
        expected_profit_per_unit=None,
        buy_price=None,
        rationale="Fallback: reject if the supplier will not accept the recommended terms.",
    ))
    candidates.sort(key=lambda item: (item.rationale != explanation, -item.score))

    return Decision(
        lot_id=offer.lot_id,
        sku=offer.sku,
        action=primary_action,
        recommended_route=recommended_route,
        ask_price=offer.ask_price,
        maximum_acceptable_buy_price=max_buy,
        target_margin=offer.target_margin,
        explanation=explanation,
        projections=projections,
        candidates=tuple(candidates),
    )
