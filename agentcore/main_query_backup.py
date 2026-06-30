#!/usr/bin/env python3
"""
Understand Anything — AgentCore Runtime Entry Point

Architecture:
- query() runs Claude Code with understand-anything plugin (Phase 0-7)
- After query() completes, Python post-processing writes to Neptune via subprocess
- Neptune write uses tools/neptune_writer.py (confirmed working Python environment)
"""

import os
import json
import glob
import subprocess
from pathlib import Path
from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import McpStdioServerConfig
from bedrock_agentcore import BedrockAgentCoreApp

BASE_DIR = Path(__file__).parent.resolve()

app = BedrockAgentCoreApp()


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
    if not payload:
        yield json.dumps({"error": "No payload provided"})
        return

    prompt = payload.get("prompt") or payload.get("message") or ""
    if not prompt:
        yield json.dumps({"error": "No prompt provided"})
        return

    os.makedirs("/tmp/workspace", exist_ok=True)
    refresh_aws_credentials()

    system_instruction = """IMPORTANT RULES:
1. NEVER ask the user questions, offer choices, or wait for confirmation. Execute ALL steps to completion without stopping.
2. When you see a GitHub URL, clone it using `git clone` in Bash tool, then run the FULL understand-anything analysis workflow (all phases 0-7) on the cloned directory. Do NOT stop after any intermediate phase.
3. When the user asks a question (no URL), answer directly using your knowledge or query_neptune tool.
4. Do NOT use WebFetch for GitHub repos. Always clone them locally.
5. After cloning, execute the understand skill to analyze the code and generate a knowledge graph. Run ALL phases without pausing.
6. When generating .understandignore or any config, accept defaults and continue immediately. NEVER ask the user to review or confirm.
7. You are in a non-interactive environment. There is no way for the user to respond mid-execution. Complete everything in one pass.
"""

    full_prompt = system_instruction + "\nUser request: " + prompt

    async for event in query(
        prompt=full_prompt,
        options=ClaudeAgentOptions(
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
            cwd="/tmp/workspace",
            max_turns=200,
            model="sonnet",
        ),
    ):
        yield event

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
