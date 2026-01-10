"""Shared mode definitions for AI SRE Agent."""
from enum import Enum


class Mode(str, Enum):
    """Agent operational modes.

    SRE: Default monitoring mode (alerts, approvals, status)
    OPERATOR: Configuration mode (memory, rules, context)
    HOME: Home automation mode (future)
    """
    SRE = "SRE"
    OPERATOR = "OPERATOR"
    HOME = "HOME"
