import os

__in_debug_mode = 'DEBUG' in os.environ  # type: bool


def in_debug_mode() -> bool:
    return __in_debug_mode
