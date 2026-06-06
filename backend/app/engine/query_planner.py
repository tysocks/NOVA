"""Viewport and LOD planning for v3 series queries."""

from __future__ import annotations

from typing import Literal

QueryMode = Literal["overview", "detail", "raw"]
FetchStrategy = Literal["aggregate", "raw_lttb", "raw"]


def resolve_fetch_strategy(
    *,
    mode: QueryMode,
    aggregation_mode: str | None,
    max_points: int | None,
) -> FetchStrategy:
    """
    Choose postgres fetch path.

    overview + cap → SQL buckets (aggregate)
    detail + cap → raw window + LTTB (raw_lttb)
    raw / none aggregation → uncapped raw (optional per_series limit elsewhere)
    """
    agg = (aggregation_mode or "auto").strip().lower()
    if mode == "raw" or agg in {"none", "raw"}:
        return "raw"
    if max_points and max_points > 0 and mode == "overview":
        return "aggregate"
    if max_points and max_points > 0:
        return "raw_lttb"
    return "raw"


def plan_points_cap(
    resolution_px: int | None,
    aggregation_mode: str | None,
    max_points: int | None,
    mode: QueryMode = "overview",
) -> int | None:
    """
    Convert viewport intent into a per-series point cap.

    Explicit max_points always wins. Raw mode disables downsampling caps.
    Overview uses 2× resolution_px; detail uses 4× for zoom windows.
    """
    if max_points and max_points > 0:
        return max_points
    if mode == "raw":
        return None
    agg = (aggregation_mode or "auto").strip().lower()
    if agg in {"none", "raw"}:
        return None
    if not resolution_px or resolution_px <= 0:
        return None
    multiplier = 4 if mode == "detail" else 2
    return max(500, min(resolution_px * multiplier, 5_000_000))


def plan_timeseries_points_cap(
    resolution_px: int | None,
    aggregation_mode: str | None,
    max_points: int | None,
) -> int | None:
    """Backward-compatible alias used by v1/v2 timeseries paths (overview only)."""
    return plan_points_cap(
        resolution_px=resolution_px,
        aggregation_mode=aggregation_mode,
        max_points=max_points,
        mode="overview",
    )
