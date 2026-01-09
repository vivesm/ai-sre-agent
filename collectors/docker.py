"""Docker container health and log collector."""

import json
import subprocess
from typing import Any


class DockerCollector:
    """Collects Docker container health status and logs."""

    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get('enabled', True)
        self.log_lines = config.get('log_lines', 100)
        self.error_only = config.get('error_only', True)

    def collect(self) -> dict:
        """Collect Docker container information."""
        if not self.enabled:
            return {'metrics': {}, 'issues': []}

        result = {
            'metrics': {
                'containers': [],
                'total': 0,
                'healthy': 0,
                'unhealthy': 0,
                'running': 0,
                'stopped': 0
            },
            'issues': []
        }

        # Get container list with health status
        containers = self._get_containers()
        result['metrics']['containers'] = containers
        result['metrics']['total'] = len(containers)

        for container in containers:
            state = container.get('state', '')
            health = container.get('health', '')

            if state == 'running':
                result['metrics']['running'] += 1
            else:
                result['metrics']['stopped'] += 1

            if health == 'healthy':
                result['metrics']['healthy'] += 1
            elif health == 'unhealthy':
                result['metrics']['unhealthy'] += 1

                # Get logs for unhealthy containers
                logs = self._get_container_logs(container['name'])

                result['issues'].append({
                    'source': 'docker',
                    'type': 'container_unhealthy',
                    'severity': 'warning',
                    'container': container['name'],
                    'image': container.get('image', ''),
                    'message': f"Container {container['name']} is unhealthy",
                    'health_status': container.get('health_log', ''),
                    'recent_logs': logs
                })

            # Check for stopped containers that should be running
            if state != 'running' and container.get('restart_policy') in ['always', 'unless-stopped']:
                result['issues'].append({
                    'source': 'docker',
                    'type': 'container_stopped',
                    'severity': 'critical',
                    'container': container['name'],
                    'message': f"Container {container['name']} is stopped but has restart policy"
                })

        return result

    def _get_containers(self) -> list:
        """Get list of all containers with their status."""
        try:
            # Get all containers with detailed info
            cmd = [
                'docker', 'ps', '-a',
                '--format', '{{json .}}'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            containers = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    container = {
                        'name': data.get('Names', ''),
                        'id': data.get('ID', ''),
                        'image': data.get('Image', ''),
                        'state': data.get('State', ''),
                        'status': data.get('Status', ''),
                    }

                    # Parse health from status string
                    status = data.get('Status', '')
                    if '(healthy)' in status:
                        container['health'] = 'healthy'
                    elif '(unhealthy)' in status:
                        container['health'] = 'unhealthy'
                    elif '(health:' in status:
                        container['health'] = 'starting'
                    else:
                        container['health'] = 'none'

                    # Get restart policy
                    inspect_result = subprocess.run(
                        ['docker', 'inspect', '--format', '{{.HostConfig.RestartPolicy.Name}}', container['name']],
                        capture_output=True, text=True, timeout=10
                    )
                    container['restart_policy'] = inspect_result.stdout.strip()

                    # Get health check log for unhealthy containers
                    if container['health'] == 'unhealthy':
                        health_result = subprocess.run(
                            ['docker', 'inspect', '--format', '{{json .State.Health}}', container['name']],
                            capture_output=True, text=True, timeout=10
                        )
                        try:
                            health_data = json.loads(health_result.stdout)
                            if health_data and health_data.get('Log'):
                                # Get last health check result
                                last_log = health_data['Log'][-1] if health_data['Log'] else {}
                                container['health_log'] = last_log.get('Output', '')[:500]
                        except json.JSONDecodeError:
                            pass

                    containers.append(container)
                except json.JSONDecodeError:
                    continue

            return containers

        except subprocess.TimeoutExpired:
            return []
        except Exception as e:
            raise RuntimeError(f"Failed to get containers: {e}")

    def _get_container_logs(self, container_name: str) -> str:
        """Get recent logs from a container."""
        try:
            cmd = ['docker', 'logs', '--tail', str(self.log_lines), container_name]
            if self.error_only:
                # Get stderr only for error logs
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                logs = result.stderr if result.stderr else result.stdout
            else:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                logs = result.stdout + result.stderr

            # Truncate to reasonable size
            return logs[-5000:] if len(logs) > 5000 else logs

        except subprocess.TimeoutExpired:
            return "[Timeout getting logs]"
        except Exception as e:
            return f"[Error getting logs: {e}]"
