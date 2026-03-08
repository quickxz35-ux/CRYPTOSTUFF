#!/usr/bin/env python3
"""Shared source routing helpers for module provider switching."""

from __future__ import annotations

from typing import Dict, List


def normalize_source_alias(source: str, aliases: Dict[str, str]) -> str:
    src = (source or "").strip().lower()
    return aliases.get(src, src)


def resolve_source_order(source: str, fallback_source: str, default_primary: str) -> List[str]:
    src = source.strip().lower()
    fb = fallback_source.strip().lower()

    if src == "auto":
        first = default_primary
    else:
        first = src

    if fb == "none":
        return [first]

    second = fb if fb else default_primary
    if second == first:
        return [first]
    return [first, second]

