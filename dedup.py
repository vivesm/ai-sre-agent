"""
Alert Deduplication - Suppress duplicate alerts to prevent spam.

Uses fingerprint-based deduplication with configurable suppression windows.
Industry best practice: https://support.squadcast.com/services/alert-deduplication-rules
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger('ai-sre-agent.dedup')


class AlertDeduplicator:
    """Deduplicate alerts based on fingerprinting."""

    def __init__(self, state_file: Path, suppress_hours: float = 2.0):
        """
        Initialize deduplicator.

        Args:
            state_file: Path to JSON file storing alert state
            suppress_hours: How long to suppress duplicate alerts (default 2h)
        """
        self.state_file = Path(state_file)
        self.suppress_hours = suppress_hours
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Load state from file."""
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except Exception as e:
                logger.warning(f"Failed to load dedup state: {e}")
        return {"seen_alerts": {}}

    def _save_state(self):
        """Persist state to file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self.state, indent=2))
        except Exception as e:
            logger.error(f"Failed to save dedup state: {e}")

    def fingerprint(self, issue: dict) -> str:
        """
        Generate unique fingerprint for an issue.

        Format: {source}:{type}:{identifier}
        Examples:
            - docker:container_unhealthy:ring-mqtt
            - system:disk_high:/_root
            - logs:error_pattern:mosquitto
        """
        source = issue.get('source', 'unknown')
        issue_type = issue.get('type', 'unknown')

        # Try various identifier fields
        identifier = (
            issue.get('container') or
            issue.get('mount') or
            issue.get('service') or
            issue.get('unit') or
            issue.get('path') or
            issue.get('name') or
            'unknown'
        )

        # Sanitize identifier (remove special chars that could break fingerprint)
        identifier = str(identifier).replace(':', '_').replace('/', '_')

        return f"{source}:{issue_type}:{identifier}"

    def should_alert(self, issue: dict) -> bool:
        """
        Check if this issue should generate an alert.

        Returns True if:
        - First time seeing this issue
        - Suppression window has expired (re-alert if still occurring)

        Returns False if:
        - Same issue seen within suppression window
        """
        fp = self.fingerprint(issue)
        now = datetime.utcnow()
        seen = self.state["seen_alerts"].get(fp)

        if not seen:
            # First time seeing this issue
            logger.info(f"New alert: {fp}")
            self.state["seen_alerts"][fp] = {
                "first_seen": now.isoformat(),
                "last_seen": now.isoformat(),
                "count": 1,
                "suppressed_until": (now + timedelta(hours=self.suppress_hours)).isoformat()
            }
            self._save_state()
            return True

        # Update last seen and count
        seen["last_seen"] = now.isoformat()
        seen["count"] += 1

        # Check if suppression expired
        try:
            suppressed_until = datetime.fromisoformat(seen["suppressed_until"])
        except (ValueError, TypeError):
            suppressed_until = now  # Invalid date, allow alert

        if now > suppressed_until:
            # Re-alert and reset suppression window
            logger.info(f"Re-alerting (suppression expired): {fp} (seen {seen['count']} times)")
            seen["suppressed_until"] = (now + timedelta(hours=self.suppress_hours)).isoformat()
            self._save_state()
            return True

        # Still suppressed
        logger.debug(f"Suppressed: {fp} (seen {seen['count']} times, until {suppressed_until})")
        self._save_state()
        return False

    def clear_resolved(self, current_issues: list):
        """
        Remove alerts that are no longer occurring.

        Call this with the current list of issues to clean up resolved ones.
        """
        current_fps = {self.fingerprint(i) for i in current_issues}
        to_remove = [fp for fp in self.state["seen_alerts"] if fp not in current_fps]

        for fp in to_remove:
            logger.info(f"Cleared resolved alert: {fp}")
            del self.state["seen_alerts"][fp]

        if to_remove:
            self._save_state()

    def get_stats(self) -> dict:
        """Get deduplication statistics."""
        alerts = self.state["seen_alerts"]
        return {
            "active_alerts": len(alerts),
            "total_suppressed": sum(a.get("count", 1) - 1 for a in alerts.values()),
            "alerts": {fp: a.get("count", 1) for fp, a in alerts.items()}
        }
