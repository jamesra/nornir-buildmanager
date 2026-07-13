"""Shared incremental-build helpers for mosaic and STOS transform refinement."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RefineSkipDecision:
    """Result of deciding whether an existing refine output can be reused."""

    skip: bool
    reason: str
    output_node: Any | None = None


class TransformRefineOrchestrator:
    """Checksum / stale-output decisions shared by RefineInvoker and GridTransform.

    Callers still own pipeline-specific image generation and refine invocation;
    this helper only answers skip / invalidate questions consistently.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def should_skip_refine(
            self,
            input_node: Any,
            output_node: Any | None,
            *,
            input_checksum: str | None = None,
            checksum_attr: str = 'InputTransformChecksum',
            locked_attr: str = 'Locked') -> RefineSkipDecision:
        """Return whether *output_node* is still valid for *input_node*.

        Prefers ``IsInputTransformMatched`` when available; otherwise compares
        ``checksum_attr`` to *input_checksum* (or ``input_node.Checksum``).
        """
        if output_node is None:
            return RefineSkipDecision(skip=False, reason='no output node', output_node=None)

        if getattr(output_node, locked_attr, False):
            return RefineSkipDecision(
                skip=True, reason='output transform is locked', output_node=output_node)

        output_path = getattr(output_node, 'FullPath', None)
        if output_path and not os.path.exists(output_path):
            return RefineSkipDecision(
                skip=False, reason='output file missing', output_node=output_node)

        matched = getattr(output_node, 'IsInputTransformMatched', None)
        if callable(matched):
            if matched(input_node):
                if output_path and self._input_file_newer_than_output(input_node, output_path):
                    return RefineSkipDecision(
                        skip=False,
                        reason='input transform file is newer than refine output',
                        output_node=output_node)
                return RefineSkipDecision(
                    skip=True,
                    reason='existing output matches input transform',
                    output_node=output_node)
            return RefineSkipDecision(
                skip=False, reason='input transform checksum mismatch', output_node=output_node)

        expected = input_checksum
        if expected is None:
            expected = getattr(input_node, 'Checksum', None)
        actual = getattr(output_node, checksum_attr, None)
        if actual is None and hasattr(output_node, 'attrib'):
            actual = output_node.attrib.get(checksum_attr)

        if expected is not None and actual == expected:
            if output_path and self._input_file_newer_than_output(input_node, output_path):
                return RefineSkipDecision(
                    skip=False,
                    reason='input transform file is newer than refine output',
                    output_node=output_node)
            return RefineSkipDecision(
                skip=True,
                reason='existing output matches input checksum',
                output_node=output_node)

        return RefineSkipDecision(
            skip=False, reason='input checksum mismatch or missing', output_node=output_node)

    def invalidate_stale_output(
            self,
            output_node: Any | None,
            reason: str,
            *,
            clean: Callable[[Any, str], None] | None = None) -> None:
        """Clean *output_node* when a stale refine product must be rebuilt."""
        if output_node is None:
            return
        self._logger.info('Invalidating refine output: %s', reason)
        if clean is not None:
            clean(output_node, reason)
            return
        cleaner = getattr(output_node, 'Clean', None)
        if callable(cleaner):
            cleaner(reason)

    @staticmethod
    def _input_file_newer_than_output(input_node: Any, output_path: str) -> bool:
        """True when the input transform file mtime is newer than the output file."""
        input_path = getattr(input_node, 'FullPath', None)
        if not input_path or not os.path.exists(input_path) or not os.path.exists(output_path):
            return False
        try:
            return os.path.getmtime(input_path) > os.path.getmtime(output_path)
        except OSError:
            return False
