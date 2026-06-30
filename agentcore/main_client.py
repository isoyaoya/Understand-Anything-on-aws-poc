#!/usr/bin/env python3
"""
Understand Anything — AgentCore Runtime with Stateful Client

Architecture improvements:
1. Uses ClaudeSDKClient (stateful) instead of query (stateless) → session continuity
2. Single source of truth pattern via build_agent_options()
3. Thin wrapper pattern — zero business logic in entrypoint
4. Post-processing writes to Neptune via subprocess (deterministic, decoupled)

Key changes from main.py:
- Replaced query() with ClaudeSDKClient context manager
- Session persists across multiple turns (if continue_conversation=True)
- Agent options defined once in build_agent_options()
"""

import os
import json
import glob
import subprocess
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import McpStdioServerConfig
from bedrock_agentcore import BedrockAgentCoreApp

BASE_DIR = Path(__file__).parent.resolve()

app = BedrockAgentCoreApp()


# === SINGLE SOURCE OF TRUTH ===
def build_agent_options(
    *,
    continue_conversation: bool = False,
    system_prompt_suffix: str | None = None,
    **overrides: Any,
) -> ClaudeAgentOptions:
    """
    Build agent configuration — the single source of truth.

    Pattern from AWS Chief of Staff example:
    - One function defines agent identity (system prompt, tools, cwd, mcp servers)
    - Both local and cloud deployments call this
    - Later modules extend via system_prompt_suffix or **overrides

    Args:
        continue_conversation: Whether to continue previous conversation
        system_prompt_suffix: Append to base system prompt (e.g., for memory injection)
        **overrides: Override any ClaudeAgentOptions field
    """

    base_system_prompt = """You are a code analysis assistant with the understand-anything plugin.
When you see a GitHub URL, clone it using git clone and run the full analysis workflow.
When the user asks a question about analyzed code, use the query_neptune tool.
"""

    system_prompt = base_system_prompt + (system_prompt_suffix or "")

    defaults: dict[str, Any] = dict(
        allowed_tools=[
            "Read", "Grep", "Glob", "Bash", "Agent",
            "mcp__neptune__clone_repository",
            "mcp__neptune__write_to_neptune",
            "mcp__neptune__query_neptune",
        ],
        mcp_servers={
            "neptune": McpStdioServerConfig(
                command="python3",
                args=[str(BASE_DIR / "tools" / "neptune_mcp_server.py")],
                env={
                    **os.environ,
                    "NEPTUNE_ENDPOINT": os.environ.get("NEPTUNE_ENDPOINT", ""),
                    "NEPTUNE_PORT": os.environ.get("NEPTUNE_PORT", "8182"),
                },
            )
        },
        system_prompt=system_prompt,
        continue_conversation=continue_conversation,
        cwd="/tmp/workspace",
        max_turns=200,
        model="sonnet",
        permission_mode="default",
        # IMPORTANT: setting_sources must include "project" to load filesystem settings:
        # CLAUDE.md, skills (.claude/skills), subagents (.claude/agents), etc.
        setting_sources=["project"],
    )
    defaults.update(overrides)  # callers override any field
    return ClaudeAgentOptions(**defaults)


def refresh_aws_credentials():
    """Export current IAM role credentials as env vars for Claude Code CLI."""
    import boto3
    session = boto3.Session()
    creds = session.get_credentials()
    if creds:
        frozen = creds.get_frozen_credentials()
        os.environ["AWS_ACCESS_KEY_ID"] = frozen.access_key
        os.environ["AWS_SECRET_ACCESS_KEY"] = frozen.secret_key
        if frozen.token:
            os.environ["AWS_SESSION_TOKEN"] = frozen.token


def write_to_neptune_subprocess(graph_path: str, project_id: str) -> dict:
    """Write graph to Neptune via subprocess (avoids gremlinpython import issue in main process)."""
    neptune_endpoint = os.environ.get("NEPTUNE_ENDPOINT", "")
    if not neptune_endpoint:
        return {"status": "skipped", "reason": "NEPTUNE_ENDPOINT not set"}

    script = str(BASE_DIR / "tools" / "neptune_writer.py")
    result = subprocess.run(
        ["python3", script, graph_path, project_id],
        capture_output=True, text=True, timeout=300,
        env={
            **os.environ,
            "NEPTUNE_ENDPOINT": neptune_endpoint,
            "NEPTUNE_PORT": os.environ.get("NEPTUNE_PORT", "8182"),
        }
    )
    if result.returncode == 0:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"status": "error", "error": f"Invalid JSON output: {result.stdout[:200]}"}
    return {"status": "error", "error": result.stderr[-500:] if result.stderr else "Unknown error"}


@app.entrypoint
async def main(payload: dict = None, context=None):
    """
    AgentCore entrypoint with stateful client.

    Key improvement: Uses ClaudeSDKClient (has session state) instead of query (stateless).
    This enables:
    - Multi-turn conversations with context retention
    - Access to conversation history
    - Session-aware tool calls
    """

    if not payload:
        yield json.dumps({"error": "No payload provided"})
        return

    prompt = payload.get("prompt") or payload.get("message") or ""
    if not prompt:
        yield json.dumps({"error": "No prompt provided"})
        return

    os.makedirs("/tmp/workspace", exist_ok=True)
    refresh_aws_credentials()

    # Get session_id from payload for conversation continuity
    session_id = payload.get("session_id")
    continue_conversation = bool(session_id)

    # Inject session context into system prompt if continuing
    system_prompt_suffix = ""
    if session_id:
        system_prompt_suffix = f"\n\n[Session ID: {session_id}] Continue the previous conversation context."

    # Build agent options from single source of truth
    options = build_agent_options(
        continue_conversation=continue_conversation,
        system_prompt_suffix=system_prompt_suffix,
    )

    # Use ClaudeSDKClient (stateful) instead of query (stateless)
    async with ClaudeSDKClient(options=options) as agent:
        # Send the query
        await agent.query(prompt=prompt)

        # Stream response
        async for msg in agent.receive_response():
            # Extract text content from message blocks
            for block in getattr(msg, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    yield text

            # Check for final result
            if hasattr(msg, "result"):
                # Optionally yield result metadata
                pass

    # === DETERMINISTIC POST-PROCESSING ===
    # Scan for generated knowledge-graph.json and write to Neptune via subprocess
    graph_files = glob.glob("/tmp/*/.understand-anything/knowledge-graph.json") + \
                  glob.glob("/tmp/workspace/*/.understand-anything/knowledge-graph.json")

    if graph_files and os.environ.get("NEPTUNE_ENDPOINT"):
        graph_path = graph_files[0]
        # Derive project_id from parent directory name
        project_dir = Path(graph_path).parent.parent
        project_id = project_dir.name

        result = write_to_neptune_subprocess(graph_path, project_id)
        yield json.dumps({
            "type": "neptune_write",
            "graph_path": graph_path,
            "project_id": project_id,
            **result,
        })


if __name__ == "__main__":
    app.run()
