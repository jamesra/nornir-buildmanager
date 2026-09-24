"""Process-wide no-delete flag for nornir-buildmanager.

When active, Clean() and file-removal helpers log what would have been deleted
but leave files and XML nodes intact.  Pipeline stages still execute; only
destructive deletion is suppressed.

Usage::

    import nornir_buildmanager.no_delete as no_delete
    no_delete.set_no_delete(True)
    ...
    if no_delete.is_no_delete():
        # skip the actual removal
"""
import logging
import os

_logger = logging.getLogger(__name__)

_no_delete: bool = False


def set_no_delete(value: bool) -> None:
    """Enable or disable no-delete mode for the current process."""
    global _no_delete
    _no_delete = bool(value)


def is_no_delete() -> bool:
    """Return True when no-delete mode is active."""
    return _no_delete


def maybe_remove_path(path: str, reason: str = "") -> bool:
    """Remove *path* from the filesystem, or log and skip when no-delete is active.

    :param path: File path to conditionally remove.
    :param reason: Human-readable reason for the removal (logged in both modes).
    :returns: True when the file was (or would have been) removed; False when
              it did not exist.
    """
    if not os.path.exists(path):
        return False

    msg = f"Would remove: {path}" + (f" ({reason})" if reason else "")
    if _no_delete:
        _logger.info("NO-DELETE %s", msg)
        return True

    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    return True
