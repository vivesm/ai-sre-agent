"""Experience replay for storing and retrieving successful interaction patterns."""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from difflib import SequenceMatcher

# Paths
DATA_DIR = Path(__file__).parent.parent / 'data'
PATTERNS_FILE = DATA_DIR / 'successful_patterns.json'


class ExperienceReplay:
    """Store and retrieve successful interaction patterns for few-shot learning."""

    def __init__(self, max_patterns: int = 100):
        self.max_patterns = max_patterns
        self.patterns = []
        self._load()

    def _load(self):
        """Load patterns from disk."""
        if PATTERNS_FILE.exists():
            try:
                with open(PATTERNS_FILE) as f:
                    self.patterns = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.patterns = []

    def _save(self):
        """Save patterns to disk."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(PATTERNS_FILE, 'w') as f:
            json.dump(self.patterns[-self.max_patterns:], f, indent=2)

    def record_success(self, query: str, response: str, category: str = 'general'):
        """Record a successful interaction.

        Args:
            query: The user's original query
            response: The successful response
            category: Category of the interaction (e.g., 'file_search', 'ssh', 'ha_control')
        """
        # Extract intent/pattern from query
        intent = self._extract_intent(query)

        pattern = {
            'query': query,
            'intent': intent,
            'response_preview': response[:200],
            'category': category,
            'timestamp': datetime.now().isoformat(),
            'success_count': 1,
        }

        # Check if similar pattern exists
        existing_idx = self._find_similar(intent)
        if existing_idx is not None:
            # Update existing pattern
            self.patterns[existing_idx]['success_count'] += 1
            self.patterns[existing_idx]['timestamp'] = pattern['timestamp']
            # Keep the most recent response
            self.patterns[existing_idx]['response_preview'] = pattern['response_preview']
        else:
            self.patterns.append(pattern)

        self._save()

    def _extract_intent(self, query: str) -> str:
        """Extract the intent/pattern from a query.

        Normalizes queries to find similar patterns.
        """
        intent = query.lower().strip()

        # Remove specific values but keep structure
        # Replace server names with placeholder
        intent = re.sub(r'\b(sagan|blackest|atom|tesseract)\b', '<server>', intent)

        # Replace file paths
        intent = re.sub(r'/[\w/.-]+', '<path>', intent)

        # Replace numbers
        intent = re.sub(r'\b\d+\b', '<num>', intent)

        # Normalize whitespace
        intent = ' '.join(intent.split())

        return intent

    def _find_similar(self, intent: str, threshold: float = 0.8) -> Optional[int]:
        """Find a similar pattern by intent.

        Args:
            intent: The normalized intent to match
            threshold: Similarity threshold (0-1)

        Returns:
            Index of similar pattern or None
        """
        for idx, pattern in enumerate(self.patterns):
            similarity = SequenceMatcher(None, intent, pattern['intent']).ratio()
            if similarity >= threshold:
                return idx
        return None

    def get_relevant_examples(self, query: str, limit: int = 3) -> List[Dict]:
        """Get relevant successful patterns for a query.

        Args:
            query: The current query
            limit: Maximum number of examples to return

        Returns:
            List of relevant pattern dicts
        """
        if not self.patterns:
            return []

        intent = self._extract_intent(query)

        # Score all patterns by similarity
        scored = []
        for pattern in self.patterns:
            similarity = SequenceMatcher(None, intent, pattern['intent']).ratio()
            # Also consider category matching
            scored.append((similarity, pattern))

        # Sort by similarity descending
        scored.sort(key=lambda x: -x[0])

        # Return top matches with similarity > 0.3
        return [p for sim, p in scored[:limit] if sim > 0.3]

    def format_as_examples(self, patterns: List[Dict]) -> str:
        """Format patterns as few-shot examples for the system prompt.

        Args:
            patterns: List of pattern dicts

        Returns:
            Formatted string for inclusion in prompt
        """
        if not patterns:
            return ""

        lines = ["## Similar Successful Queries"]
        for p in patterns:
            lines.append(f"- Query: {p['query'][:80]}...")
            lines.append(f"  Response: {p['response_preview'][:100]}...")
        return '\n'.join(lines)

    def get_category(self, query: str) -> str:
        """Determine the category of a query.

        Args:
            query: The user's query

        Returns:
            Category string
        """
        query_lower = query.lower()

        if any(word in query_lower for word in ['ssh', 'sagan', 'blackest', 'tesseract']):
            return 'ssh_remote'
        elif any(word in query_lower for word in ['file', 'largest', 'disk', 'storage']):
            return 'file_search'
        elif any(word in query_lower for word in ['light', 'lamp', 'turn on', 'turn off', 'switch']):
            return 'ha_control'
        elif any(word in query_lower for word in ['docker', 'container', 'restart']):
            return 'docker'
        elif any(word in query_lower for word in ['uptime', 'status', 'health', 'memory', 'cpu']):
            return 'system_status'
        else:
            return 'general'

    def stats(self) -> Dict:
        """Get statistics about stored patterns.

        Returns:
            Dict with pattern statistics
        """
        if not self.patterns:
            return {'total': 0, 'categories': {}}

        categories = {}
        for p in self.patterns:
            cat = p.get('category', 'general')
            categories[cat] = categories.get(cat, 0) + 1

        return {
            'total': len(self.patterns),
            'categories': categories,
            'most_successful': sorted(
                self.patterns,
                key=lambda x: x.get('success_count', 1),
                reverse=True
            )[:5]
        }


# Global instance for easy access
_replay = None


def get_replay() -> ExperienceReplay:
    """Get the global ExperienceReplay instance."""
    global _replay
    if _replay is None:
        _replay = ExperienceReplay()
    return _replay


def main():
    """Test the experience replay system."""
    replay = ExperienceReplay()

    # Show current stats
    stats = replay.stats()
    print(f"Stored patterns: {stats['total']}")
    print(f"Categories: {stats['categories']}")

    if stats['most_successful']:
        print("\nMost successful patterns:")
        for p in stats['most_successful']:
            print(f"  [{p.get('success_count', 1)}x] {p['query'][:60]}...")


if __name__ == '__main__':
    main()
