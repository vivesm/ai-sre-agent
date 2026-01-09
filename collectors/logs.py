"""Log file and journal collector."""

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class LogCollector:
    """Collects recent errors from logs and journal."""

    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get('enabled', True)
        self.paths = config.get('paths', [])
        self.journal_units = config.get('journal_units', [])
        self.lookback_minutes = config.get('lookback_minutes', 30)
        self.error_patterns = config.get('error_patterns', [
            'error', 'Error', 'ERROR',
            'failed', 'Failed', 'FAILED',
            'critical', 'Critical', 'CRITICAL',
            'exception', 'Exception', 'EXCEPTION',
            'panic', 'Panic', 'PANIC'
        ])

    def collect(self) -> dict:
        """Collect log errors."""
        if not self.enabled:
            return {'metrics': {}, 'issues': []}

        result = {
            'metrics': {
                'journal_errors': 0,
                'file_errors': 0
            },
            'issues': []
        }

        # Check journal for recent errors
        journal_errors = self._get_journal_errors()
        result['metrics']['journal_errors'] = len(journal_errors)

        if journal_errors:
            # Group by unit/identifier
            grouped = {}
            for entry in journal_errors:
                unit = entry.get('unit', 'unknown')
                if unit not in grouped:
                    grouped[unit] = []
                grouped[unit].append(entry)

            for unit, entries in grouped.items():
                if len(entries) >= 3:  # Only report if multiple errors
                    result['issues'].append({
                        'source': 'logs',
                        'type': 'journal_errors',
                        'severity': 'warning',
                        'unit': unit,
                        'count': len(entries),
                        'sample': entries[:5],  # First 5 entries
                        'message': f"Found {len(entries)} errors in journal for {unit}"
                    })

        # Check configured log files
        for log_path in self.paths:
            file_errors = self._get_file_errors(log_path)
            result['metrics']['file_errors'] += len(file_errors)

            if file_errors:
                result['issues'].append({
                    'source': 'logs',
                    'type': 'log_file_errors',
                    'severity': 'info',
                    'file': log_path,
                    'count': len(file_errors),
                    'sample': file_errors[:10],
                    'message': f"Found {len(file_errors)} errors in {log_path}"
                })

        return result

    def _get_journal_errors(self) -> list:
        """Get recent errors from systemd journal."""
        errors = []
        since = datetime.utcnow() - timedelta(minutes=self.lookback_minutes)
        since_str = since.strftime('%Y-%m-%d %H:%M:%S')

        try:
            # Get priority 0-3 (emerg, alert, crit, err)
            cmd = [
                'journalctl',
                '--since', since_str,
                '--priority', 'err',
                '--output', 'json',
                '--no-pager',
                '-n', '100'  # Limit to 100 entries
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            import json
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    errors.append({
                        'unit': entry.get('_SYSTEMD_UNIT', entry.get('SYSLOG_IDENTIFIER', 'unknown')),
                        'message': entry.get('MESSAGE', '')[:500],
                        'timestamp': entry.get('__REALTIME_TIMESTAMP', '')
                    })
                except json.JSONDecodeError:
                    continue

        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

        return errors

    def _get_file_errors(self, log_path: str) -> list:
        """Get recent errors from a log file."""
        errors = []
        path = Path(log_path)

        if not path.exists():
            return errors

        try:
            # Get last N lines
            result = subprocess.run(
                ['tail', '-n', '500', str(path)],
                capture_output=True, text=True, timeout=10
            )

            for line in result.stdout.split('\n'):
                if any(pattern in line for pattern in self.error_patterns):
                    errors.append(line[:500])

        except Exception:
            pass

        return errors
