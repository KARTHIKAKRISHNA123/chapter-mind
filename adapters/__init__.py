"""
adapters/__init__.py
====================
Public re-exports for the adapters package.
"""
from .base import DocumentAdapter, OutputWriter
from .registry import get_adapter

__all__ = ["DocumentAdapter", "OutputWriter", "get_adapter"]
