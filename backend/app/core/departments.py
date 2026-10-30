DEPARTMENTS = ("production", "maintenance", "hr", "safety", "quality", "stores")

_ALIASES = {"quality control": "quality", "qc": "quality"}


def canonical_department(value: str | None) -> str | None:
    """Return the canonical department code, or None if the value is not a known department."""
    if not value:
        return None
    key = value.strip().lower()
    key = _ALIASES.get(key, key)
    return key if key in DEPARTMENTS else None
