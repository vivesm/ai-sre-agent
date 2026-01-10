"""System metrics collector (disk, CPU, memory, services)."""

import os
import subprocess
from typing import Any


class SystemCollector:
    """Collects system health metrics."""

    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get('enabled', True)
        self.disk_threshold = config.get('disk_threshold', 85)
        self.memory_threshold = config.get('memory_threshold', 90)
        self.load_threshold = config.get('load_threshold', None)  # Auto-detect based on CPUs

    def collect(self) -> dict:
        """Collect system metrics."""
        if not self.enabled:
            return {'metrics': {}, 'issues': []}

        result = {
            'metrics': {},
            'issues': []
        }

        # Collect disk usage
        disk_info = self._get_disk_usage()
        result['metrics']['disk'] = disk_info
        for mount, usage in disk_info.items():
            if usage['percent'] >= self.disk_threshold:
                severity = 'critical' if usage['percent'] >= 95 else 'warning'
                result['issues'].append({
                    'source': 'system',
                    'type': 'disk_space_low',
                    'severity': severity,
                    'mount': mount,
                    'percent': usage['percent'],
                    'available': usage['available'],
                    'message': f"Disk {mount} is {usage['percent']}% full ({usage['available']} available)"
                })

        # Collect memory usage
        memory_info = self._get_memory_usage()
        result['metrics']['memory'] = memory_info
        if memory_info['percent'] >= self.memory_threshold:
            result['issues'].append({
                'source': 'system',
                'type': 'memory_high',
                'severity': 'warning',
                'percent': memory_info['percent'],
                'available': memory_info['available'],
                'message': f"Memory usage is {memory_info['percent']}% ({memory_info['available']} available)"
            })

        # Collect load average
        load_info = self._get_load_average()
        result['metrics']['load'] = load_info
        threshold = self.load_threshold or load_info['cpu_count']
        if load_info['load_1m'] > threshold * 2:
            result['issues'].append({
                'source': 'system',
                'type': 'load_high',
                'severity': 'warning',
                'load_1m': load_info['load_1m'],
                'cpu_count': load_info['cpu_count'],
                'message': f"System load is {load_info['load_1m']} (threshold: {threshold})"
            })

        # Check failed systemd services
        failed_services = self._get_failed_services()
        result['metrics']['failed_services'] = failed_services
        for service in failed_services:
            result['issues'].append({
                'source': 'system',
                'type': 'service_failed',
                'severity': 'warning',
                'service': service['name'],
                'message': f"Systemd service {service['name']} has failed"
            })

        # Check network connectivity
        network_ok = self._check_network()
        result['metrics']['network'] = {'connected': network_ok}
        if not network_ok:
            result['issues'].append({
                'source': 'system',
                'type': 'network_down',
                'severity': 'critical',
                'message': "Network connectivity check failed"
            })

        return result

    def _get_disk_usage(self) -> dict:
        """Get disk usage for important mounts."""
        mounts_to_check = ['/', '/docker', '/home', '/var']
        disk_info = {}

        try:
            result = subprocess.run(
                ['df', '-h', '--output=target,pcent,avail'],
                capture_output=True, text=True, timeout=10
            )

            for line in result.stdout.strip().split('\n')[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 3:
                    mount = parts[0]
                    if mount in mounts_to_check or mount == '/':
                        percent = int(parts[1].rstrip('%'))
                        available = parts[2]
                        disk_info[mount] = {
                            'percent': percent,
                            'available': available
                        }

        except Exception:
            pass

        return disk_info

    def _get_memory_usage(self) -> dict:
        """Get memory usage statistics."""
        try:
            result = subprocess.run(
                ['free', '-h'],
                capture_output=True, text=True, timeout=10
            )

            for line in result.stdout.strip().split('\n'):
                if line.startswith('Mem:'):
                    parts = line.split()
                    total = parts[1]
                    used = parts[2]
                    available = parts[6] if len(parts) > 6 else parts[3]

                    # Calculate percentage from raw bytes
                    result_bytes = subprocess.run(
                        ['free', '-b'],
                        capture_output=True, text=True, timeout=10
                    )
                    for bline in result_bytes.stdout.strip().split('\n'):
                        if bline.startswith('Mem:'):
                            bparts = bline.split()
                            total_b = int(bparts[1])
                            used_b = int(bparts[2])
                            percent = round((used_b / total_b) * 100, 1)
                            break
                    else:
                        percent = 0

                    return {
                        'total': total,
                        'used': used,
                        'available': available,
                        'percent': percent
                    }

        except Exception:
            pass

        return {'total': '0', 'used': '0', 'available': '0', 'percent': 0}

    def _get_load_average(self) -> dict:
        """Get system load average."""
        try:
            load_1m, load_5m, load_15m = os.getloadavg()
            cpu_count = os.cpu_count() or 1

            return {
                'load_1m': round(load_1m, 2),
                'load_5m': round(load_5m, 2),
                'load_15m': round(load_15m, 2),
                'cpu_count': cpu_count
            }
        except Exception:
            return {'load_1m': 0, 'load_5m': 0, 'load_15m': 0, 'cpu_count': 1}

    def _get_failed_services(self) -> list:
        """Get list of failed systemd services."""
        try:
            result = subprocess.run(
                ['systemctl', 'list-units', '--failed', '--no-legend', '--plain'],
                capture_output=True, text=True, timeout=10
            )

            failed = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split()
                    if parts:
                        service_name = parts[0]
                        # Skip certain known transient failures
                        if not any(skip in service_name for skip in ['apt-', 'snapd']):
                            failed.append({'name': service_name})

            return failed

        except Exception:
            return []

    def _check_network(self) -> bool:
        """Check network connectivity with multiple targets.

        Tries multiple DNS providers to avoid false positives from
        a single dropped packet or slow host.
        """
        targets = ['8.8.8.8', '1.1.1.1', '9.9.9.9']

        for target in targets:
            try:
                result = subprocess.run(
                    ['ping', '-c', '2', '-W', '3', target],
                    capture_output=True, timeout=10
                )
                if result.returncode == 0:
                    return True  # Any target reachable = network OK
            except Exception:
                continue

        return False  # All targets failed
