"""Shared mode definitions for AI SRE Agent."""
from enum import Enum


class Mode(str, Enum):
    """Agent operational modes.

    SRE: Default monitoring mode (alerts, approvals, status, HA control)
    OPERATOR: Configuration mode (memory, rules, context)
    """
    SRE = "SRE"
    OPERATOR = "OPERATOR"
