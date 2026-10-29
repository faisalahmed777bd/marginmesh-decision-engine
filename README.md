# MarginMesh IQ: Lot-Level Commercial Decision Engine

An explainable, deterministic Python decision core for evaluating a wholesale electronics stock-lot offer across multiple resale routes and time horizons.

The feature selected for this starter is the plan's strongest technical differentiator: **represent each stock lot as its own commercial state, then compare risk-adjusted action outcomes across routes and time**. A generic SKU margin is not enough. Two lots of the same model can have different supplier risk, landed cost, warranty exposure, route costs, and expected resale prices.

This repository is a small **rules-based proof of concept**. It demonstrates the calculation and recommendation logic; it is not the full MarginMesh IQ product, an autonomous trading system, or a trained AI/ML model.

## What it does

Given a supplier ask, a lot quantity, a home route, route-specific resale estimates, costs, and a minimum margin threshold, the engine:

1. Builds a separate projection for each route at day 0, 14, 30, and 60.
2. Calculates per-unit and whole-lot net profit after purchase price, supplier risk, freight, duties, handling, financing, warranty/return exposure, and route-risk cost.
3. Calculates the margin at each horizon, using resale price as the denominator.
4. Selects a transparent next action:
   - **BUY** when the home route clears the target margin and no material route improvement is available.
   - **REROUTE** when another route clears the target margin and beats the home route by the configured margin advantage, or when the home route fails the floor but another route clears it.
   - **NEGOTIATE** when no route clears the floor at the asking price, but a positive maximum purchase price can meet the floor.
   - **REJECT** when no route clears the floor and the inputs provide no positive negotiation ceiling.
5. Returns the selected route, maximum acceptable purchase price where applicable, ranked action candidates, and a plain-language explanation.

All inputs and output calculations remain visible. The engine does not silently invent prices, supplier scores, demand, or probabilities.

## Quick start

Requires Python 3.10 or later. The package has no runtime dependencies.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

## Example

```python
from marginmesh_engine import LotOffer, RouteEconomics, recommend_action

offer = LotOffer(
    lot_id="LOT-001",
    sku="Apex Model X 256GB",
    quantity=500,
    ask_price=410.00,
    home_route="UK",
    target_margin=0.05,
    reroute_advantage=0.01,
    supplier_risk_cost=0.00,
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
    ),
)

decision = recommend_action(offer)
print(decision.action.value)
print(decision.recommended_route)
print(decision.maximum_acceptable_buy_price)
print(decision.explanation)
```

With these illustrative assumptions, neither route reaches the 5% floor at £410. The engine identifies UAE as the route with the best purchase ceiling and recommends negotiating to **£407.20 or less**. If the ask is changed to £388, UAE clears the floor and the action changes to **REROUTE**.

To inspect the route and time comparisons:

```python
for projection in decision.projections:
    print(
        projection.route,
        projection.day,
        round(projection.net_profit_per_unit, 2),
        round(projection.margin * 100, 2),
    )
```

## Core calculation

For route `r` and horizon `t`:

```text
adjusted_cost(r) = ask_price
                 + supplier_risk_cost
                 + freight(r)
                 + duties(r)
                 + handling(r)
                 + financing(r)
                 + warranty_and_returns(r)
                 + route_risk_cost(r)

net_profit_per_unit(r, t) = resale_price(r, t) - adjusted_cost(r)
net_profit_total(r, t) = net_profit_per_unit(r, t) * quantity
margin(r, t) = net_profit_per_unit(r, t) / resale_price(r, t)

maximum_acceptable_buy_price(r) = resale_price(r, 0) * (1 - target_margin)
                                  - supplier_risk_cost
                                  - non_purchase_cost(r)
```

The maximum acceptable buy price is floored at zero. It is the price ceiling that exactly reaches the configured target margin under the supplied day-0 assumptions. It is not a market quote or a guarantee that a supplier will accept the negotiation.

### Commercial Action Index

Each candidate's 0–100 score is derived from its margin relative to the target:

```text
score = clamp(round(50 + (margin - target_margin) * 500), 0, 100)
```

That makes 50 the target-margin boundary and adds five score points for each percentage point above the target. The score is a ranking aid, **not a probability or confidence level**. NEGOTIATE receives 50 because its proposed ceiling reaches the target exactly; REJECT receives 0. The selected recommendation is governed by explicit feasibility and route rules, not by a black-box model.

## Project structure

```text
marginmesh-decision-engine/
├── README.md
├── pyproject.toml
├── src/
│   └── marginmesh_engine/
│       ├── __init__.py
│       └── engine.py
└── tests/
    └── test_engine.py
```

## Input model

### `LotOffer`

| Field | Meaning |
|---|---|
| `lot_id` | Unique identifier for this offer or stock lot. |
| `sku` | Normalized product identifier or description. |
| `quantity` | Number of units in the lot. |
| `ask_price` | Supplier asking price per unit. |
| `home_route` | The route used as the baseline for reroute comparison. |
| `routes` | One or more `RouteEconomics` alternatives. |
| `target_margin` | Required minimum margin, e.g. `0.05` for 5%. |
| `reroute_advantage` | Minimum margin advantage over home route to call the action `REROUTE`. |
| `supplier_risk_cost` | User-assessed supplier-risk adjustment per unit. |

### `RouteEconomics`

`resale_prices` must contain keys `0`, `14`, `30`, and `60`, with price estimates in the same currency as the offer. Route cost fields are non-negative per-unit amounts. `route_risk_cost` and `supplier_risk_cost` are transparent monetary adjustments: the current code does not estimate risk probabilities.

## Tests

The test suite covers:

- a negotiation ceiling and route choice;
- the transition from NEGOTIATE to REROUTE when the offer price falls;
- route-change threshold behavior;
- rejection when no positive negotiation ceiling exists;
- all route-by-horizon projections and lot-level scaling;
- invalid horizons, negative costs, invalid route names, and invalid margin floors.

Run with:

```bash
python -m unittest discover -s tests -v
```

## Scope and limitations

- The code receives resale estimates as inputs. It does not ingest files, identify SKUs, fetch prices, or forecast resale values.
- Day 14/30/60 values are caller-supplied scenarios, not model predictions.
- Supplier, warranty, and route risk are cost adjustments provided by a user or upstream system. The implementation does not estimate claims, fraud, delivery reliability, or probability of loss.
- The decision applies the target-margin rule to day 0. Future projections are shown to support review; they do not change the primary action yet.
- It handles one offer at a time and does not optimize a portfolio, cash constraint, tax treatment, or shared logistics capacity.
- The score is not a probability, confidence grade, investment recommendation, or trading instruction.
- The implementation is a starting point for validation against anonymized real lots, not evidence of predictive accuracy or commercial performance.

## Suggested next increments

1. Add a versioned input/output JSON schema and persist every assumption used for a recommendation.
2. Add anonymized real-lot fixtures and compare results with trader decisions and realized margins.
3. Add confidence based on data completeness and sample support, separate from the action score.
4. Add negotiated-price scenarios for multiple routes and show the sensitivity of the chosen action to price changes.
5. Add auditable overrides and link recommendation, human decision, transaction, and realized outcome.
6. Only after collecting adequate, permitted outcome data, assess whether predictive models improve on the transparent baseline.

## Development note

The rules and equations here are an engineering demonstration based on the proposed MarginMesh IQ feature. They should be reviewed with electronics-trading specialists, validated with real data, and versioned before any operational or customer-facing use.
