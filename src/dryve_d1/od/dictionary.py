"""Compatibility shim: ObjectDictionary lives in od.indices but is importable here.

    from ..od.dictionary import ObjectDictionary
"""

from .indices import ObjectDictionary  # noqa: F401

__all__ = ["ObjectDictionary"]