from ..config import settings


def resolve_source_db_name(source: str | None, db_name: str | None) -> str | None:
    """
    Resolve the effective database based on a logical source selector.
    Explicit db_name always wins for backward compatibility.
    """
    if db_name:
        return db_name
    mode = (source or "auto").strip().lower()
    if mode in {"auto", "measured", "simulation"}:
        return None
    if mode == "redscale":
        return getattr(settings, "db_name_redscale", None) or None
    if mode == "bluescale":
        return getattr(settings, "db_name_bluescale", None) or None
    return None


def resolve_overlay_targets(
    source: str | None,
    overlay_mode: str | None,
    db_name: str | None,
) -> list[tuple[str, str | None]]:
    """
    Return [(source_label, db_name_override)] query targets.
    """
    if db_name:
        return [("explicit", db_name)]
    mode = (overlay_mode or "single").strip().lower()
    src = (source or "auto").strip().lower()
    if mode in {"both", "overlay"}:
        red = resolve_source_db_name("redscale", None)
        blue = resolve_source_db_name("bluescale", None)
        out: list[tuple[str, str | None]] = []
        if red:
            out.append(("redscale", red))
        if blue:
            out.append(("bluescale", blue))
        if out:
            return out
    return [(src if src else "auto", resolve_source_db_name(source, None))]
