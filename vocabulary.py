"""
vocabulary.py — ROOT-LEVEL SHIM
================================
Re-exports from utils.vocabulary so that legacy imports
    from . import vocabulary as V
still work after the module was moved to utils/.
"""
from .utils.vocabulary import *          # noqa: F401,F403
from .utils.vocabulary import (          # explicit for type checkers
    RANKS, MATTER, parse_number, match_division,
)
