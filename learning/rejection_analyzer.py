"""Analyze rejection patterns from historical plans to learn from failures."""
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

# Paths
DATA_DIR = Path(__file__).parent.parent / 'data'
HISTORY_DIR = DATA_DIR / 'history'
MEMORY_FILE = Path(__file__).parent.parent / '.claude' / 'memory.md'


class RejectionAnalyzer:
    """Analyzes rejected plans to extract learnable patterns."""

    def __init__(self):
        self.plans = []
        self.patterns = defaultdict(list)

    def load_history(self) -> List[dict]:
        """Load all historical plans."""
        self.plans = []
        if not HISTORY_DIR.exists():
            return []

        for f in sorted(HISTORY_DIR.glob('*.json')):
            try:
                with open(f) as fp:
                    plan = json.load(fp)
                    plan['_file'] = f.name
                    self.plans.append(plan)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not load {f}: {e}")

        return self.plans

    def analyze(self) -> Dict:
        """Analyze rejection patterns and return summary."""
        if not self.plans:
            self.load_history()

        results = {
            'total_plans': len(self.plans),
            'rejected': 0,
            'completed': 0,
            'other': 0,
            'rejection_categories': defaultdict(int),
            'false_positive_patterns': [],
            'successful_patterns': [],
        }

        for plan in self.plans:
            status = plan.get('status', 'unknown')
            if status == 'rejected':
                results['rejected'] += 1
                reason = plan.get('rejection_reason', 'Unknown')
                category = self._categorize_rejection(reason)
                results['rejection_categories'][category] += 1

                # Extract pattern from rejected plan
                pattern = self._extract_pattern(plan)
                if pattern:
                    results['false_positive_patterns'].append(pattern)

            elif status == 'completed':
                results['completed'] += 1
                # Learn from successful plans
                success = self._extract_success_pattern(plan)
                if success:
                    results['successful_patterns'].append(success)
            else:
                results['other'] += 1

        # Calculate rates
        if results['total_plans'] > 0:
            results['rejection_rate'] = results['rejected'] / results['total_plans']
            results['success_rate'] = results['completed'] / results['total_plans']
        else:
            results['rejection_rate'] = 0
            results['success_rate'] = 0

        return results

    def _categorize_rejection(self, reason: str) -> str:
        """Categorize rejection reason into a type."""
        reason_lower = reason.lower()

        if 'false positive' in reason_lower:
            if 'network' in reason_lower:
                return 'false_positive_network'
            return 'false_positive_other'
        elif 'superseded' in reason_lower:
            return 'superseded'
        elif 'user' in reason_lower:
            return 'user_override'
        else:
            return 'other'

    def _extract_pattern(self, plan: dict) -> dict:
        """Extract learnable pattern from a rejected plan."""
        summary = plan.get('summary', '')
        evidence = plan.get('evidence', [])
        reason = plan.get('rejection_reason', '')

        # Look for network false positive pattern
        if 'network' in summary.lower() and 'containers' in summary.lower():
            healthy_containers = any('healthy' in str(e).lower() or 'running' in str(e).lower()
                                     for e in evidence)
            if healthy_containers:
                return {
                    'type': 'network_false_positive',
                    'condition': 'network check failed + containers healthy',
                    'action': 'suppress',
                    'confidence_adjustment': -0.5,
                    'learned_from': plan.get('plan_id', 'unknown'),
                    'learned_at': datetime.now().isoformat(),
                }

        return None

    def _extract_success_pattern(self, plan: dict) -> dict:
        """Extract pattern from a successful plan."""
        return {
            'summary': plan.get('summary', '')[:100],
            'severity': plan.get('severity', 'unknown'),
            'plan_id': plan.get('plan_id', 'unknown'),
            'steps_count': len(plan.get('plan', [])),
        }

    def get_suppression_rules(self) -> List[dict]:
        """Generate suppression rules from rejection patterns."""
        if not self.plans:
            self.load_history()

        rules = []

        # Count patterns
        network_fp_count = sum(
            1 for p in self.plans
            if p.get('status') == 'rejected'
            and 'network' in p.get('summary', '').lower()
            and 'false positive' in p.get('rejection_reason', '').lower()
        )

        # If pattern appears 3+ times, create suppression rule
        if network_fp_count >= 3:
            rules.append({
                'name': 'network_healthy_containers',
                'description': 'Suppress network alerts when all containers are healthy',
                'condition': 'network.connected == false AND containers.unhealthy == 0',
                'action': 'suppress',
                'occurrences': network_fp_count,
                'confidence_threshold': 0.7,  # Only alert if confidence > 0.7
            })

        return rules

    def update_memory(self) -> str:
        """Update .claude/memory.md with learned patterns."""
        results = self.analyze()
        rules = self.get_suppression_rules()

        # Build memory content
        false_positive_section = []
        for rule in rules:
            false_positive_section.append(
                f"- **{rule['name']}**: {rule['description']} "
                f"(learned from {rule['occurrences']} rejections, {datetime.now().strftime('%Y-%m-%d')})"
            )

        successful_section = []
        for pattern in results['successful_patterns']:
            successful_section.append(
                f"- Plan {pattern['plan_id']}: {pattern['summary']} "
                f"({pattern['severity']}, {pattern['steps_count']} steps)"
            )

        # Read current memory
        if MEMORY_FILE.exists():
            with open(MEMORY_FILE) as f:
                content = f.read()
        else:
            content = "# Agent Memory\n\n## Learned Entity Mappings\n\n## Successful Remediation Patterns\n\n## False Positives\n\n## User Preferences\n\n---\n"

        # Update False Positives section
        fp_content = '\n'.join(false_positive_section) if false_positive_section else '<!-- None learned yet -->'
        content = re.sub(
            r'(## False Positives\n).*?(\n## |\n---)',
            f'\\1{fp_content}\n\n\\2',
            content,
            flags=re.DOTALL
        )

        # Update Successful Remediation Patterns section
        success_content = '\n'.join(successful_section) if successful_section else '<!-- None learned yet -->'
        content = re.sub(
            r'(## Successful Remediation Patterns\n).*?(\n## )',
            f'\\1{success_content}\n\n\\2',
            content,
            flags=re.DOTALL
        )

        # Add last updated timestamp
        content = re.sub(
            r'\n---\n\*Created:.*',
            f'\n---\n*Last analyzed: {datetime.now().strftime("%Y-%m-%d %H:%M")}*\n',
            content
        )

        # Write updated memory
        with open(MEMORY_FILE, 'w') as f:
            f.write(content)

        return content

    def print_report(self):
        """Print analysis report to stdout."""
        results = self.analyze()
        rules = self.get_suppression_rules()

        print("=" * 60)
        print("REJECTION ANALYSIS REPORT")
        print("=" * 60)
        print(f"\nTotal plans analyzed: {results['total_plans']}")
        print(f"Rejected: {results['rejected']} ({results['rejection_rate']*100:.1f}%)")
        print(f"Completed: {results['completed']} ({results['success_rate']*100:.1f}%)")

        print("\n--- Rejection Categories ---")
        for cat, count in sorted(results['rejection_categories'].items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}")

        print("\n--- Learned Suppression Rules ---")
        for rule in rules:
            print(f"  [{rule['name']}]")
            print(f"    {rule['description']}")
            print(f"    Condition: {rule['condition']}")
            print(f"    Based on {rule['occurrences']} rejections")

        print("\n--- Successful Patterns ---")
        for pattern in results['successful_patterns']:
            print(f"  {pattern['plan_id']}: {pattern['summary'][:60]}...")

        print("\n" + "=" * 60)


def main():
    """Run analysis and update memory."""
    analyzer = RejectionAnalyzer()
    analyzer.load_history()

    # Print report
    analyzer.print_report()

    # Update memory file
    print("\nUpdating .claude/memory.md...")
    analyzer.update_memory()
    print("Done!")


if __name__ == '__main__':
    main()
