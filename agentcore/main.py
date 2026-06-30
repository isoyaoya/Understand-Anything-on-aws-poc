#!/usr/bin/env python3
"""
Understand Anything — AgentCore Runtime (POC 3-Step with S3 persistence)

Step 1: Analyze repo (GitHub URL → /understand → JSON → S3 + /mnt/workspace)
Step 2: Write to Neptune (select project from S3 if needed → download → write)
Step 3: Query (select project from S3 if needed → query Neptune)

All steps use real-time yield (SSE streaming).
Session header ensures /mnt/workspace persists across steps.
continue_conversation=True preserves Claude Code dialog history.
"""

import os
import sys
import json
import glob
from pathlib import Path
from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import McpStdioServerConfig
from bedrock_agentcore import BedrockAgentCoreApp

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR / "tools"))

from s3_manager import extract_project_id, upload_graph, list_projects, download_graph, delete_project
from neptune_writer import write_graph, delete_graph

app = BedrockAgentCoreApp()

SESSION_STATE_FILE = "/mnt/workspace/.session_state.json"


def load_session_state() -> dict:
    try:
        with open(SESSION_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_session_state(state: dict):
    with open(SESSION_STATE_FILE, "w") as f:
        json.dump(state, f)


def refresh_aws_credentials():
    import boto3
    session = boto3.Session()
    creds = session.get_credentials()
    if creds:
        frozen = creds.get_frozen_credentials()
        os.environ["AWS_ACCESS_KEY_ID"] = frozen.access_key
        os.environ["AWS_SECRET_ACCESS_KEY"] = frozen.secret_key
        if frozen.token:
            os.environ["AWS_SESSION_TOKEN"] = frozen.token


def detect_intent_keyword(prompt: str) -> str:
    """Keyword fallback for intent detection."""
    lower = prompt.lower().strip()
    if "github.com" in lower:
        return "analyze"
    neptune_commands = [
        "write to neptune", "write neptune", "write to graph",
        "write graph", "sync to neptune", "push to neptune",
    ]
    if any(cmd in lower for cmd in neptune_commands):
        return "neptune"
    if lower in ("write", "sync"):
        return "neptune"
    delete_commands = [
        "delete project", "delete", "remove project", "remove",
        "drop project", "删除", "删除项目",
    ]
    if any(cmd in lower for cmd in delete_commands):
        return "delete"
    return "query"


INTENT_CLASSIFICATION_PROMPT = """Classify the user's message into exactly ONE category. Reply with ONLY the category name.

Categories:
- "analyze" — user provides a GitHub URL and wants to analyze the repository
- "neptune" — user explicitly asks to write, sync, or push data to Neptune or the graph database
- "delete" — user wants to delete or remove a project (from S3 and Neptune)
- "query" — user asks a question about code, architecture, or anything else

User message: {prompt}

Category:"""


async def detect_intent(prompt: str) -> str:
    """Use LLM to classify intent, with keyword fallback."""
    try:
        result_text = ""
        async for event in query(
            prompt=INTENT_CLASSIFICATION_PROMPT.format(prompt=prompt),
            options=ClaudeAgentOptions(
                allowed_tools=[],
                max_turns=1,
                model="haiku",
            ),
        ):
            if isinstance(event, str):
                try:
                    data = json.loads(event)
                    content = data.get("content", [])
                    for block in content:
                        if isinstance(block, dict) and block.get("text"):
                            result_text += block["text"]
                except (json.JSONDecodeError, TypeError):
                    result_text += event

        intent = result_text.strip().lower().strip('"\'')
        if intent in ("analyze", "neptune", "query", "delete"):
            return intent
    except Exception:
        pass

    return detect_intent_keyword(prompt)


def check_neptune_exists(project_id: str) -> bool:
    """Check if project already exists in Neptune via HTTP POST."""
    endpoint = os.environ.get("NEPTUNE_ENDPOINT", "")
    print(f"[main] check_neptune_exists: project_id={project_id!r}, NEPTUNE_ENDPOINT={endpoint!r}", flush=True)
    if not endpoint:
        print("[main] check_neptune_exists: NEPTUNE_ENDPOINT empty, returning False", flush=True)
        return False
    try:
        from neptune_http import NeptuneHttpClient, escape_gremlin
        client = NeptuneHttpClient(endpoint=endpoint)
        pid = escape_gremlin(project_id)
        result = client.execute(
            f"g.V().has('project_id', '{pid}').hasLabel('project').count()"
        )
        count = client._parse_count(result)
        print(f"[main] check_neptune_exists result: count={count}", flush=True)
        return count > 0
    except Exception as e:
        print(f"[main] check_neptune_exists ERROR: {type(e).__name__}: {e}", flush=True)
        return False


def is_project_selection(prompt: str, projects: list) -> str | None:
    """Check if the user's message matches a project_id from the list."""
    text = prompt.strip()
    for p in projects:
        if text == p.get("project_id", ""):
            return text
    return None


@app.entrypoint
async def main(payload: dict = None, context=None):
    if not payload:
        yield json.dumps({"error": "No payload provided"})
        return

    prompt = payload.get("prompt") or payload.get("message") or ""
    if not prompt:
        yield json.dumps({"error": "No prompt provided"})
        return

    os.makedirs("/mnt/workspace", exist_ok=True)
    refresh_aws_credentials()

    # Load session state from persistent file
    state = load_session_state()
    project_id = state.get("project_id")
    pending_intent = state.get("pending_intent")

    # Check if user is selecting a project from a previous prompt
    projects = list_projects()
    selected = is_project_selection(prompt, projects)
    if selected:
        project_id = selected
        state["project_id"] = project_id
        state.pop("pending_intent", None)
        save_session_state(state)

        # Auto-execute pending intent if exists
        if pending_intent == "neptune":
            prompt = "write to neptune"
        elif pending_intent == "delete":
            prompt = f"delete {selected}"
        elif pending_intent == "query":
            yield json.dumps({"content": [{"text": f"Selected project: **{selected}**. Please ask your question now."}]})
            yield json.dumps({"subtype": "success", "result": f"Project {selected} selected. Please ask your question."})
            return
        else:
            yield json.dumps({"content": [{"text": f"Selected project: **{selected}**. You can now ask questions or write to Neptune."}]})
            yield json.dumps({"subtype": "success", "result": f"Project {selected} selected. Send a question or type 'write neptune'."})
            return

    intent = await detect_intent(prompt)

    if intent == "analyze":
        # === STEP 1: Analyze repo ===
        github_url = ""
        for word in prompt.split():
            if "github.com" in word:
                github_url = word.strip("\"'<>")
                break

        pid = extract_project_id(github_url) if github_url else "default"
        state["project_id"] = pid
        state.pop("pending_intent", None)
        save_session_state(state)

        system_instruction = """IMPORTANT RULES:
1. NEVER ask the user questions, offer choices, or wait for confirmation. Execute ALL steps to completion without stopping.
2. When you see a GitHub URL, clone it to /mnt/workspace/ using `git clone <URL> /mnt/workspace/<repo_name>` in Bash tool, then run the FULL understand-anything analysis workflow (all phases 0-7) on the cloned directory. Do NOT stop after any intermediate phase.
3. Do NOT use WebFetch for GitHub repos. Always clone them locally.
4. After cloning, execute the understand skill to analyze the code and generate a knowledge graph. Run ALL phases without pausing.
5. When generating .understandignore or any config, accept defaults and continue immediately. NEVER ask the user to review or confirm.
6. You are in a non-interactive environment. There is no way for the user to respond mid-execution. Complete everything in one pass.
"""
        full_prompt = system_instruction + "\nUser request: " + prompt

        async for event in query(
            prompt=full_prompt,
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Grep", "Glob", "Bash", "Agent"],
                mcp_servers={
                    "neptune": McpStdioServerConfig(
                        command="python3",
                        args=[str(BASE_DIR / "tools" / "neptune_mcp_server.py")],
                        env={**os.environ,
                             "NEPTUNE_ENDPOINT": os.environ.get("NEPTUNE_ENDPOINT", ""),
                             "NEPTUNE_PORT": os.environ.get("NEPTUNE_PORT", "8182")},
                    )
                },
                cwd="/mnt/workspace",
                max_turns=200,
                model="sonnet",
                continue_conversation=True,
            ),
        ):
            yield event

        # Post-analysis: upload to S3
        graph_files = glob.glob("/mnt/workspace/*/.understand-anything/knowledge-graph.json")
        if graph_files:
            graph_path = graph_files[0]
            yield json.dumps({"content": [{"text": f"Uploading analysis results to S3 ({pid})..."}]})
            result = upload_graph(pid, graph_path, github_url)
            if result.get("status") == "success":
                yield json.dumps({"content": [{"text": f"Uploaded to S3: {result.get('s3_key', '')}"}]})
            else:
                yield json.dumps({"content": [{"text": f"S3 upload failed: {result.get('error', 'unknown')}"}]})

    elif intent == "neptune":
        # === STEP 2: Write to Neptune ===
        if not project_id:
            # Check local files
            graph_files = glob.glob("/mnt/workspace/*/.understand-anything/knowledge-graph.json")
            if graph_files:
                project_id = Path(graph_files[0]).parent.parent.name
                state["project_id"] = project_id
                save_session_state(state)
            else:
                # Ask user to select from S3
                if not projects:
                    yield json.dumps({"content": [{"text": "No projects found. Please provide a GitHub URL to analyze first."}]})
                    yield json.dumps({"subtype": "success", "result": "No projects available. Please provide a GitHub URL to analyze."})
                    return

                state["pending_intent"] = "neptune"
                save_session_state(state)

                options_text = "\n".join([f"- `{p['project_id']}`" for p in projects])
                yield json.dumps({
                    "content": [{"text": f"Please select a project to write to Neptune:\n\n{options_text}"}],
                    "type": "project_selection",
                    "options": [p["project_id"] for p in projects]
                })
                yield json.dumps({"subtype": "success", "result": "Waiting for project selection..."})
                return

        # Check if project already exists in Neptune
        if check_neptune_exists(project_id):
            yield json.dumps({"content": [{"text": f"Project **{project_id}** already exists in the graph database. No need to write again."}]})
            yield json.dumps({"subtype": "success", "result": f"Project {project_id} already exists in Neptune. You can start asking questions."})
            return

        # Find or download the graph JSON
        graph_path = None
        local_files = glob.glob("/mnt/workspace/*/.understand-anything/knowledge-graph.json")
        for f in local_files:
            if project_id in f:
                graph_path = f
                break

        if not graph_path:
            download_dir = f"/mnt/workspace/{project_id}"
            os.makedirs(download_dir, exist_ok=True)
            local_path = f"{download_dir}/knowledge-graph.json"
            downloaded = download_graph(project_id, local_path)
            if downloaded:
                graph_path = downloaded
            else:
                yield json.dumps({"content": [{"text": f"Could not find analysis data for project {project_id}."}]})
                yield json.dumps({"subtype": "success", "result": f"No data found for project {project_id}."})
                return

        yield json.dumps({"content": [{"text": f"Writing to Neptune ({project_id})..."}]})

        progress_messages = []

        def on_progress(msg: str):
            progress_messages.append(msg)

        try:
            data = write_graph(graph_path, project_id, progress_callback=on_progress)
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield json.dumps({"content": [{"text": f"Neptune write failed: {type(e).__name__}: {e}"}]})
            yield json.dumps({"subtype": "success", "result": f"Neptune write failed: {type(e).__name__}: {e}"})
            return

        print(f"[main] write_graph returned: {data}", flush=True)
        status = data.get("status", "error")
        nodes_written = data.get("nodes_written", 0)
        nodes_attempted = data.get("nodes_attempted", 0)
        edges_written = data.get("edges_written", 0)
        edges_attempted = data.get("edges_attempted", 0)
        verified = data.get("verified_node_count", 0)

        if status == "success":
            yield json.dumps({"content": [{"text": f"Neptune write verified: {verified} nodes, {edges_written} edges written successfully."}]})
            yield json.dumps({"subtype": "success", "result": f"Neptune write complete! {verified} nodes and {edges_written} edges verified. Ready for queries."})
        elif status == "partial":
            yield json.dumps({"content": [{"text": f"Neptune write partial: {nodes_written}/{nodes_attempted} nodes, {edges_written}/{edges_attempted} edges. Verified: {verified} nodes."}]})
            if data.get("failed_nodes"):
                sample = ", ".join(n["id"] for n in data["failed_nodes"][:3])
                yield json.dumps({"content": [{"text": f"Failed samples: {sample}"}]})
            yield json.dumps({"subtype": "success", "result": f"Neptune write partially complete. {verified} nodes verified."})
        else:
            err = data.get("error", f"nodes={nodes_written}/{nodes_attempted}, verified={verified}")
            yield json.dumps({"content": [{"text": f"Neptune write failed: {err}"}]})
            yield json.dumps({"subtype": "success", "result": f"Neptune write failed: {err}"})

    elif intent == "delete":
        # === DELETE: Remove project from S3 + Neptune ===
        # Try to extract project_id from the user's message
        target_project = None
        for p in projects:
            if p.get("project_id", "") in prompt:
                target_project = p["project_id"]
                break

        if not target_project and project_id:
            # Check if user just says "delete" without specifying which
            # If there's a selected project in session, ask for confirmation
            target_project = project_id

        if not target_project:
            if not projects:
                yield json.dumps({"content": [{"text": "No projects found to delete."}]})
                yield json.dumps({"subtype": "success", "result": "No projects available."})
                return

            state["pending_intent"] = "delete"
            save_session_state(state)

            options_text = "\n".join([f"- `{p['project_id']}`" for p in projects])
            yield json.dumps({
                "content": [{"text": f"Please select a project to delete:\n\n{options_text}"}],
                "type": "project_selection",
                "options": [p["project_id"] for p in projects]
            })
            yield json.dumps({"subtype": "success", "result": "Waiting for project selection..."})
            return

        yield json.dumps({"content": [{"text": f"Deleting project **{target_project}**..."}]})

        # Step 1: Delete from Neptune
        neptune_result = delete_graph(target_project)
        neptune_status = neptune_result.get("status", "error")
        if neptune_status == "success":
            yield json.dumps({"content": [{"text": f"Neptune: all vertices and edges deleted."}]})
        elif neptune_status == "error" and "NEPTUNE_ENDPOINT not set" in neptune_result.get("error", ""):
            yield json.dumps({"content": [{"text": "Neptune: skipped (endpoint not configured)."}]})
        else:
            yield json.dumps({"content": [{"text": f"Neptune: {neptune_result}"}]})

        # Step 2: Delete from S3
        s3_result = delete_project(target_project)
        s3_status = s3_result.get("status", "error")
        if s3_status == "success":
            yield json.dumps({"content": [{"text": f"S3: knowledge graph and index entry deleted."}]})
        else:
            yield json.dumps({"content": [{"text": f"S3: {s3_result}"}]})

        # Clear session state if deleted the current project
        if state.get("project_id") == target_project:
            state.pop("project_id", None)
            state.pop("pending_intent", None)
            save_session_state(state)

        if neptune_status in ("success", "error") and s3_status == "success":
            yield json.dumps({"subtype": "success", "result": f"Project {target_project} deleted successfully."})
        else:
            yield json.dumps({"subtype": "success", "result": f"Project {target_project} deletion completed with issues. Neptune: {neptune_status}, S3: {s3_status}"})

    else:
        # === STEP 3: Query ===
        if not project_id:
            if not projects:
                yield json.dumps({"content": [{"text": "No projects found. Please provide a GitHub URL to analyze first."}]})
                yield json.dumps({"subtype": "success", "result": "No projects available. Please provide a GitHub URL to analyze."})
                return

            state["pending_intent"] = "query"
            save_session_state(state)

            options_text = "\n".join([f"- `{p['project_id']}`" for p in projects])
            yield json.dumps({
                "content": [{"text": f"Please select a project to query:\n\n{options_text}"}],
                "type": "project_selection",
                "options": [p["project_id"] for p in projects]
            })
            yield json.dumps({"subtype": "success", "result": "Waiting for project selection..."})
            return

        system_instruction = f"""You are a code analysis assistant. Answer the user's question using the query_neptune MCP tool.
The current project_id is: {project_id}

IMPORTANT: For answering questions about code, ALWAYS use query_type="build_chat_context" with project_id="{project_id}" and a keyword extracted from the user's question. This performs a multi-field search, 1-hop graph expansion, and returns formatted context with nodes, edges, layers, and relationships.

The formatted_context field in the response contains a ready-to-use markdown summary. Use it to provide a clear, structured answer referencing specific files, functions, and relationships.

Other query types (get_nodes_by_type, get_layers, get_tour, get_full_graph) are for specific lookups only.

If Neptune has no data, say so and suggest the user analyze a repo first."""

        full_prompt = system_instruction + "\nUser question: " + prompt

        async for event in query(
            prompt=full_prompt,
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Grep", "Glob", "Bash",
                               "mcp__neptune__query_neptune"],
                mcp_servers={
                    "neptune": McpStdioServerConfig(
                        command="python3",
                        args=[str(BASE_DIR / "tools" / "neptune_mcp_server.py")],
                        env={**os.environ,
                             "NEPTUNE_ENDPOINT": os.environ.get("NEPTUNE_ENDPOINT", ""),
                             "NEPTUNE_PORT": os.environ.get("NEPTUNE_PORT", "8182")},
                    )
                },
                cwd="/mnt/workspace",
                max_turns=50,
                model="sonnet",
                continue_conversation=True,
            ),
        ):
            yield event


if __name__ == "__main__":
    app.run()
