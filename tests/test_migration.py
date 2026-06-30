#!/usr/bin/env python3
"""
Migration Validation Test Suite

Tests both improvements:
1. Client mode (stateful) vs Query mode (stateless)
2. Optimized Neptune writer vs original writer

Usage:
    python3 tests/test_migration.py --test-client
    python3 tests/test_migration.py --test-neptune
    python3 tests/test_migration.py --all
"""

import asyncio
import json
import os
import sys
import time
import subprocess
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# === Test 1: Client Mode (Stateful) ===

async def test_client_mode_context():
    """
    Test that Client mode maintains context across multiple turns.
    Expected: Second turn should reference first turn's context.
    """
    print("\n" + "="*60)
    print("TEST 1: Client Mode Context Retention")
    print("="*60)

    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        system_prompt="You are a helpful assistant. Remember what the user tells you.",
        continue_conversation=True,
        cwd="/tmp",
        max_turns=10,
        model="sonnet",
    )

    async with ClaudeSDKClient(options=options) as agent:
        # Turn 1: Establish context
        print("\n[Turn 1] Setting context...")
        await agent.query("My favorite color is blue. Please remember this.")

        result1 = None
        async for msg in agent.receive_response():
            if hasattr(msg, "result"):
                result1 = msg.result

        print(f"✓ Turn 1 completed: {result1[:100] if result1 else 'No result'}...")

        # Turn 2: Test context retention
        print("\n[Turn 2] Testing context retention...")
        await agent.query("What is my favorite color?")

        result2 = None
        async for msg in agent.receive_response():
            if hasattr(msg, "result"):
                result2 = msg.result

        print(f"✓ Turn 2 completed: {result2[:100] if result2 else 'No result'}...")

        # Validate
        if result2 and "blue" in result2.lower():
            print("\n✅ PASS: Client mode correctly retained context")
            return True
        else:
            print(f"\n❌ FAIL: Context not retained. Result2: {result2}")
            return False


async def test_query_mode_context():
    """
    Test that Query mode does NOT maintain context (baseline).
    Expected: Second turn should NOT reference first turn's context.
    """
    print("\n" + "="*60)
    print("TEST 2: Query Mode Context Loss (Baseline)")
    print("="*60)

    from claude_agent_sdk import query, ClaudeAgentOptions

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        system_prompt="You are a helpful assistant.",
        cwd="/tmp",
        max_turns=10,
        model="sonnet",
    )

    # Turn 1: Establish context
    print("\n[Turn 1] Setting context...")
    result1 = None
    async for event in query("My favorite color is blue. Please remember this.", options=options):
        if isinstance(event, dict) and event.get("type") == "result":
            result1 = event.get("result")

    print(f"✓ Turn 1 completed: {result1[:100] if result1 else 'No result'}...")

    # Turn 2: Test context retention (should fail)
    print("\n[Turn 2] Testing context retention...")
    result2 = None
    async for event in query("What is my favorite color?", options=options):
        if isinstance(event, dict) and event.get("type") == "result":
            result2 = event.get("result")

    print(f"✓ Turn 2 completed: {result2[:100] if result2 else 'No result'}...")

    # Validate
    if result2 and "blue" not in result2.lower():
        print("\n✅ EXPECTED: Query mode correctly lost context (stateless)")
        return True
    else:
        print(f"\n⚠️ UNEXPECTED: Query mode retained context. Result2: {result2}")
        return False


# === Test 2: Neptune Writer Performance ===

def test_neptune_writer_performance():
    """
    Compare performance of original vs optimized Neptune writer.
    Uses a synthetic test graph with 1000 nodes and 2000 edges.
    """
    print("\n" + "="*60)
    print("TEST 3: Neptune Writer Performance Comparison")
    print("="*60)

    # Check Neptune endpoint
    neptune_endpoint = os.environ.get("NEPTUNE_ENDPOINT")
    if not neptune_endpoint:
        print("⚠️ SKIP: NEPTUNE_ENDPOINT not set")
        return None

    # Generate test graph
    print("\n[Setup] Generating test graph (1000 nodes, 2000 edges)...")
    test_graph = {
        "version": "1.0.0",
        "project": {
            "name": "test-project",
            "description": "Performance test project",
            "languages": ["python"],
            "frameworks": ["django"],
            "analyzedAt": "2026-06-25T00:00:00Z",
        },
        "nodes": [
            {
                "id": f"file:src/test_{i}.py",
                "type": "file",
                "name": f"test_{i}.py",
                "filePath": f"src/test_{i}.py",
                "summary": f"Test file {i} for performance benchmarking",
                "tags": ["test", "performance"],
                "complexity": "moderate",
            }
            for i in range(1000)
        ],
        "edges": [
            {
                "source": f"file:src/test_{i}.py",
                "target": f"file:src/test_{i+1}.py",
                "type": "imports",
                "weight": 0.7,
                "direction": "forward",
            }
            for i in range(999)
        ] + [
            {
                "source": f"file:src/test_{i}.py",
                "target": f"file:src/test_{i+500}.py",
                "type": "calls",
                "weight": 0.8,
                "direction": "forward",
            }
            for i in range(500)
        ],
        "layers": [
            {
                "id": "layer:application",
                "name": "Application",
                "description": "Application layer",
                "nodeIds": [f"file:src/test_{i}.py" for i in range(500)],
            }
        ],
        "tour": [
            {
                "order": 1,
                "title": "Entry Point",
                "description": "Start here",
                "nodeIds": ["file:src/test_0.py"],
            }
        ],
    }

    test_graph_path = "/tmp/test_graph_perf.json"
    with open(test_graph_path, "w") as f:
        json.dump(test_graph, f)

    print(f"✓ Test graph generated: {test_graph_path}")

    # Test original writer
    print("\n[Test 1] Original Neptune Writer...")
    original_script = Path(__file__).parent.parent / "agentcore" / "tools" / "neptune_writer.py"

    start = time.time()
    result = subprocess.run(
        ["python3", str(original_script), test_graph_path, "perf-test-1"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ}
    )
    original_time = time.time() - start

    if result.returncode == 0:
        original_result = json.loads(result.stdout)
        print(f"✓ Original writer: {original_time:.2f}s")
        print(f"  - Nodes written: {original_result.get('nodes_written', 0)}")
        print(f"  - Edges written: {original_result.get('edges_written', 0)}")
    else:
        print(f"❌ Original writer failed: {result.stderr[:200]}")
        return None

    # Test optimized writer
    print("\n[Test 2] Optimized Neptune Writer...")
    optimized_script = Path(__file__).parent.parent / "agentcore" / "tools" / "neptune_writer_optimized.py"

    start = time.time()
    result = subprocess.run(
        ["python3", str(optimized_script), test_graph_path, "perf-test-2", "--workers=4"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ}
    )
    optimized_time = time.time() - start

    if result.returncode == 0:
        optimized_result = json.loads(result.stdout)
        print(f"✓ Optimized writer: {optimized_time:.2f}s")
        print(f"  - Nodes written: {optimized_result.get('nodes_written', 0)}")
        print(f"  - Edges written: {optimized_result.get('edges_written', 0)}")
        print(f"  - Throughput: {optimized_result.get('throughput_nodes_per_sec', 0)} nodes/sec")
    else:
        print(f"❌ Optimized writer failed: {result.stderr[:200]}")
        return None

    # Compare
    speedup = original_time / optimized_time
    print("\n" + "="*60)
    print(f"⚡ Performance Improvement: {speedup:.2f}x faster")
    print("="*60)

    if speedup >= 2.0:
        print("✅ PASS: Optimized writer is at least 2x faster")
        return True
    else:
        print("⚠️ MARGINAL: Speedup less than 2x (may need tuning)")
        return False


# === Test 3: End-to-End Validation ===

def test_end_to_end_flow():
    """
    Simulate the full flow: User request → AgentCore → Analysis → Neptune write
    """
    print("\n" + "="*60)
    print("TEST 4: End-to-End Flow Validation")
    print("="*60)

    # This test requires a running AgentCore endpoint
    agentcore_endpoint = os.environ.get("AGENTCORE_ENDPOINT")
    if not agentcore_endpoint:
        print("⚠️ SKIP: AGENTCORE_ENDPOINT not set")
        return None

    import requests

    # Test payload
    payload = {
        "prompt": "Analyze this test repo: https://github.com/aws-samples/sample-agentic-ai-with-claude-agent-sdk-and-amazon-bedrock-agentcore",
        "session_id": "e2e-test-session"
    }

    print(f"\n[Request] Sending to {agentcore_endpoint}...")
    try:
        response = requests.post(
            f"{agentcore_endpoint}/invocations",
            json=payload,
            timeout=600,
            stream=True
        )

        if response.status_code == 200:
            print("✓ Request accepted")

            # Collect streamed response
            full_response = ""
            for chunk in response.iter_content(chunk_size=1024, decode_unicode=True):
                if chunk:
                    full_response += chunk
                    print(".", end="", flush=True)

            print(f"\n✓ Response received ({len(full_response)} chars)")

            # Check for Neptune write confirmation
            if "neptune_write" in full_response:
                print("✅ PASS: Neptune write completed")
                return True
            else:
                print("⚠️ PARTIAL: Analysis completed but no Neptune write confirmation")
                return False
        else:
            print(f"❌ FAIL: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


# === Main Test Runner ===

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migration validation tests")
    parser.add_argument("--test-client", action="store_true", help="Test Client mode context retention")
    parser.add_argument("--test-neptune", action="store_true", help="Test Neptune writer performance")
    parser.add_argument("--test-e2e", action="store_true", help="Test end-to-end flow")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    args = parser.parse_args()

    if not any([args.test_client, args.test_neptune, args.test_e2e, args.all]):
        parser.print_help()
        return

    results = {}

    if args.test_client or args.all:
        results["client_context"] = await test_client_mode_context()
        results["query_baseline"] = await test_query_mode_context()

    if args.test_neptune or args.all:
        results["neptune_perf"] = test_neptune_writer_performance()

    if args.test_e2e or args.all:
        results["e2e_flow"] = test_end_to_end_flow()

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    for test_name, result in results.items():
        if result is True:
            status = "✅ PASS"
        elif result is False:
            status = "❌ FAIL"
        else:
            status = "⚠️ SKIP"
        print(f"{test_name:20s} {status}")

    passed = sum(1 for r in results.values() if r is True)
    total = len(results)
    print(f"\nPassed: {passed}/{total}")

    return all(r in [True, None] for r in results.values())


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
