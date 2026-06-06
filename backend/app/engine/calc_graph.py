"""Dependency ordering for calculated channels (Phase 5)."""

from __future__ import annotations

from ..models import CalculatedChannelSpec


def _channel_ref(name: str) -> str:
    return name.split("|")[-1] if "|" in name else name


def order_calculated_channels(specs: list[CalculatedChannelSpec]) -> list[CalculatedChannelSpec]:
    """
    Topological order: formulas/rolling that depend on other calculated names run later.
    """
    if not specs:
        return []
    by_name = {s.name: s for s in specs}
    calc_names = set(by_name)

    def deps(spec: CalculatedChannelSpec) -> set[str]:
        out: set[str] = set()
        for ch in spec.channels or []:
            ref = _channel_ref(ch)
            if ref in calc_names and ref != spec.name:
                out.add(ref)
        return out

    ordered: list[CalculatedChannelSpec] = []
    pending = list(specs)
    guard = 0
    while pending and guard < len(pending) * len(pending) + 1:
        guard += 1
        progressed = False
        next_pending: list[CalculatedChannelSpec] = []
        done_names = {s.name for s in ordered}
        for spec in pending:
            if deps(spec).issubset(done_names):
                ordered.append(spec)
                done_names.add(spec.name)
                progressed = True
            else:
                next_pending.append(spec)
        pending = next_pending
        if progressed:
            continue
        ordered.extend(pending)
        break
    return ordered
