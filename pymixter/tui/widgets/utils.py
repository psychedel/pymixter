"""Shared utilities for TUI widgets."""


def resample(data: list[float], width: int) -> list[float]:
    """Resample data to target width using max pooling.

    Each output bin takes the maximum value from its corresponding
    source range, which preserves peaks in waveform/energy data.

    Returns an empty list if *data* is empty or *width* is zero.
    """
    if not data or width <= 0:
        return []
    n = len(data)
    result = []
    for i in range(width):
        src_start = int(i * n / width)
        src_end = max(src_start + 1, int((i + 1) * n / width))
        chunk = data[src_start:src_end]
        result.append(max(chunk) if chunk else 0.0)
    return result
