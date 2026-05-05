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
