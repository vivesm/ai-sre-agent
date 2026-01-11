"""Shared mode definitions for AI SRE Agent."""
from enum import Enum


class Mode(str, Enum):
    """Agent operational mode.

    SRE: Unified mode for all operations (alerts, device control, memory, rules)
    """
    SRE = "SRE"
