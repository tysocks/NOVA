from app.engine.query_planner import plan_points_cap, resolve_fetch_strategy


def test_plan_points_cap_overview_multiplier():
    assert plan_points_cap(resolution_px=600, aggregation_mode="auto", max_points=None, mode="overview") == 1200


def test_resolve_fetch_strategy_no_cap_returns_raw():
    assert resolve_fetch_strategy(mode="overview", aggregation_mode="auto", max_points=None) == "raw"
