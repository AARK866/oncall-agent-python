from typing import Any


def matches_metadata(metadata: dict[str, Any], metadata_filter: dict[str, Any]) -> bool:
    for key, expected in metadata_filter.items():
        if expected is None:
            continue

        actual = metadata.get(key)
        if actual is None:
            return False

        actual_values = _as_normalized_set(actual)
        expected_values = _as_normalized_set(expected)
        if actual_values.isdisjoint(expected_values):
            return False

    return True


def _as_normalized_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.lower()}
    if isinstance(value, (list, tuple, set)):
        return {str(item).lower() for item in value}
    return {str(value).lower()}
