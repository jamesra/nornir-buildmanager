"""Optional SAM2 predictor loader. Torch/SAM2 are not part of the default install."""

from __future__ import annotations

from typing import Any, Callable

Predictor = Callable[..., dict[str, Any]]


def load_predictor(checkpoint: str | None = None) -> Predictor:
    """Return a SAM2 scoring callable, or raise if the optional stack is missing.

    The gallery image must never import this module. ScoreAnnotationCrops runs
    on the build machine (nornir:prod / cursor-dev). Wiring a real SAM2
    checkpoint is later work; tests inject a stub Predictor.
    """
    del checkpoint
    try:
        import sam2  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "SAM2 is not installed. ScoreAnnotationCrops needs the optional "
            "sam2 stack on the build machine, not the gallery image."
        ) from exc
    raise RuntimeError(
        "SAM2 scoring is not wired yet. Pass Predictor= for tests, or install "
        "and register a checkpoint on the build machine."
    )
