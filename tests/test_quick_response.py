#!/usr/bin/env python3
"""Regression tests for quick response patterns.

Tests the hybrid intent router pattern:
1. BYPASS_PATTERNS -> Always route to Claude
2. SIMPLE_PATTERNS + CLARITY_KEYWORDS -> Quick response
3. Everything else -> Claude
"""

import sys
sys.path.insert(0, '..')
from signal_chat import (
    is_simple_query, get_quick_response,
    SIMPLE_PATTERNS, BYPASS_PATTERNS, MEMORY_CLARITY_KEYWORDS
)

# Mock system context for testing
MOCK_CONTEXT = """CPU Temp: 45.0°C
Memory: 8000MB/16000MB (50% used)
Disk: /: 44% used (100GB/233GB)
Containers: 27 running, 0 unhealthy
Public IP: 1.2.3.4
Uptime: up 5 days"""


class TestBypassPatterns:
    """Test that meta-questions about the bot route to Claude."""

    def test_memory_bank_bypasses(self):
        """'memory bank' should NOT be a quick response."""
        queries = [
            "What's in your memory bank?",
            "Show me your memory bank",
            "Tell me about your memory bank",
        ]
        for q in queries:
            assert not is_simple_query(q), f"Should bypass: {q}"

    def test_self_referential_queries_bypass(self):
        """Questions about the bot itself should route to Claude."""
        queries = [
            "What do you know about the servers?",
            "Tell me about your memory",
            "What have you learned?",
            "Show me your knowledge base",
            "What can you do?",
            "Tell me about yourself",
        ]
        for q in queries:
            assert not is_simple_query(q), f"Should bypass: {q}"

    def test_ambiguous_memory_bypasses(self):
        """'memory' without clarity keywords should route to Claude."""
        queries = [
            "memory",  # Ambiguous
            "check memory",  # No clarity keyword
            "how's memory",  # No clarity keyword
        ]
        for q in queries:
            assert not is_simple_query(q), f"Should bypass (ambiguous): {q}"


class TestRAMQueries:
    """Test that actual RAM queries get quick responses."""

    def test_memory_with_clarity_keywords(self):
        """'memory' + clarity keyword = quick response."""
        queries = [
            "memory usage",
            "how much memory is used",
            "memory available",
            "free memory",
            "memory percent",
            "memory in gb",
            "memory in mb",
            "what's the memory %",
        ]
        for q in queries:
            assert is_simple_query(q), f"Should be simple: {q}"
            response = get_quick_response(q, MOCK_CONTEXT)
            assert response is not None, f"Should get response for: {q}"
            assert 'MB' in response or '50%' in response, f"Should have memory info: {response}"

    def test_ram_keyword_always_works(self):
        """'ram' is unambiguous and should always work."""
        queries = ["ram", "ram usage", "how much ram"]
        for q in queries:
            assert is_simple_query(q), f"Should be simple: {q}"
            response = get_quick_response(q, MOCK_CONTEXT)
            assert response is not None, f"Should get response for: {q}"


class TestOtherQuickResponses:
    """Test that other quick responses still work correctly."""

    def test_cpu_temp(self):
        for q in ["cpu temp", "cpu temperature", "temp"]:
            assert is_simple_query(q), f"Should be simple: {q}"
            response = get_quick_response(q, MOCK_CONTEXT)
            assert '45' in response, f"Expected temp in: {response}"

    def test_uptime(self):
        assert is_simple_query("uptime")
        response = get_quick_response("uptime", MOCK_CONTEXT)
        assert '5 days' in response

    def test_disk(self):
        assert is_simple_query("disk")
        response = get_quick_response("disk", MOCK_CONTEXT)
        assert '44%' in response

    def test_ip(self):
        assert is_simple_query("ip")
        response = get_quick_response("ip", MOCK_CONTEXT)
        assert '1.2.3.4' in response

    def test_containers(self):
        assert is_simple_query("containers")
        response = get_quick_response("containers", MOCK_CONTEXT)
        assert '27' in response


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_long_queries_route_to_claude(self):
        """Queries over 50 chars should route to Claude."""
        long_query = "What is the current memory usage on the server right now please?"
        assert len(long_query) > 50
        assert not is_simple_query(long_query), "Long queries should bypass"

    def test_case_insensitivity(self):
        """Patterns should be case-insensitive."""
        assert is_simple_query("MEMORY USAGE")
        assert is_simple_query("Memory Usage")
        assert not is_simple_query("MEMORY BANK")

    def test_partial_matches_handled(self):
        """'memory' in other words shouldn't false-match."""
        # This tests that we're matching word boundaries reasonably
        assert not is_simple_query("remind me to check memory bank")


def run_all_tests():
    """Run all test classes."""
    import traceback

    test_classes = [
        TestBypassPatterns,
        TestRAMQueries,
        TestOtherQuickResponses,
        TestEdgeCases,
    ]

    passed = 0
    failed = 0

    for cls in test_classes:
        print(f"\n{cls.__name__}:")
        instance = cls()
        for method_name in sorted(dir(instance)):
            if method_name.startswith('test_'):
                try:
                    getattr(instance, method_name)()
                    passed += 1
                    print(f"  ✓ {method_name}")
                except AssertionError as e:
                    failed += 1
                    print(f"  ✗ {method_name}: {e}")
                except Exception as e:
                    failed += 1
                    print(f"  ✗ {method_name}: {traceback.format_exc()}")

    print(f"\n{'✅' if failed == 0 else '❌'} {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
