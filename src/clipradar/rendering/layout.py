from __future__ import annotations

from collections.abc import Iterable, Mapping


Region = tuple[float, float, float, float]
REGION_KEYS = ("gameplay", "facecam", "hud")


def default_output_regions(mode: str, available: Iterable[str] = REGION_KEYS) -> dict[str, Region]:
    """Return stable portrait slots, adjusted to the source regions that exist."""
    available_keys = set(available)
    if mode != "gaming_split":
        return {"gameplay": (0.0, 0.0, 1.0, 1.0)}

    has_facecam = "facecam" in available_keys
    has_hud = "hud" in available_keys
    context_height = 0.29 if has_facecam or has_hud else 0.0
    result: dict[str, Region] = {
        "gameplay": (0.0, context_height, 1.0, 1.0 - context_height),
    }
    if has_facecam and has_hud:
        result["facecam"] = (0.0, 0.0, 0.5, context_height)
        result["hud"] = (0.5, 0.0, 0.5, context_height)
    elif has_facecam:
        result["facecam"] = (0.0, 0.0, 1.0, context_height)
    elif has_hud:
        result["hud"] = (0.0, 0.0, 1.0, context_height)
    return result


def normalize_output_regions(value: Mapping[str, object] | None) -> dict[str, Region]:
    """Normalize persisted JSON and discard incomplete or unsafe rectangles."""
    result: dict[str, Region] = {}
    for key, raw in (value or {}).items():
        if key not in REGION_KEYS or not isinstance(raw, (list, tuple)) or len(raw) != 4:
            continue
        try:
            x, y, width, height = (float(item) for item in raw)
        except (TypeError, ValueError):
            continue
        minimum = 0.04 if key == "gameplay" else 0.03
        if (
            width >= minimum
            and height >= minimum
            and x >= 0
            and y >= 0
            and x + width <= 1.001
            and y + height <= 1.001
        ):
            result[key] = (
                round(x, 5),
                round(y, 5),
                round(width, 5),
                round(height, 5),
            )
    return result


def resolved_output_regions(
    mode: str,
    saved: Mapping[str, object] | None,
    available: Iterable[str],
) -> dict[str, Region]:
    """Use explicit user slots when present and safe defaults otherwise."""
    available_keys = set(available)
    result = default_output_regions(mode, available_keys)
    result.update({
        key: region
        for key, region in normalize_output_regions(saved).items()
        if key in available_keys or key == "gameplay"
    })
    return result
