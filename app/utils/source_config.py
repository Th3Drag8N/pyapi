"""
Source provider order/selection logic.
"""

DEFAULT_SOURCE_ORDER = ["anikage", "animex", "anidap"]


def normalize_source_name(value: str | None) -> str | None:
    if not value:
        return None
    lower = str(value).lower()
    return lower if lower in DEFAULT_SOURCE_ORDER else None


def get_source_order(requested: str | None = None) -> list[str]:
    norm = normalize_source_name(requested)
    if norm:
        return [norm, *[s for s in DEFAULT_SOURCE_ORDER if s != norm]]
    return list(DEFAULT_SOURCE_ORDER)
