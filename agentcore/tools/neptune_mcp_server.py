#!/usr/bin/env python3
"""MCP Server for Neptune graph operations + git clone."""

import asyncio
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

NEPTUNE_ENDPOINT = os.environ.get("NEPTUNE_ENDPOINT", "")
NEPTUNE_PORT = os.environ.get("NEPTUNE_PORT", "8182")
CLONE_BASE_DIR = "/tmp/repos"

server = Server("neptune-tools")
_executor = ThreadPoolExecutor(max_workers=4)


def _run_gremlin_query(query_str: str):
    """Run a single Gremlin query in a thread (avoids event loop conflict)."""
    from gremlin_python.driver import client as gremlin_client
    endpoint = f"wss://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/gremlin"
    c = gremlin_client.Client(endpoint, "g")
    try:
        return c.submit(query_str).all().result()
    finally:
        c.close()


def _run_gremlin_queries(queries: list[str]):
    """Run multiple Gremlin queries sequentially in a thread, return list of results."""
    from gremlin_python.driver import client as gremlin_client
    endpoint = f"wss://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/gremlin"
    c = gremlin_client.Client(endpoint, "g")
    try:
        results = []
        for q in queries:
            results.append(c.submit(q).all().result())
        return results
    finally:
        c.close()


async def gremlin_query(query_str: str):
    """Async wrapper: run Gremlin query in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_gremlin_query, query_str)


async def gremlin_queries(queries: list[str]):
    """Async wrapper: run multiple Gremlin queries in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_gremlin_queries, queries)


def escape_gremlin(s: str) -> str:
    """Escape string for Gremlin inline query (single quotes)."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


def format_context_markdown(project, nodes, edges, layers):
    """Format query results as markdown matching /understand-chat output format."""
    lines = []

    # Project header
    p_name = project.get("name", "") if isinstance(project, dict) else ""
    p_desc = project.get("description", "") if isinstance(project, dict) else ""
    p_langs = project.get("languages", "") if isinstance(project, dict) else ""
    p_fws = project.get("frameworks", "") if isinstance(project, dict) else ""

    if isinstance(p_langs, str):
        try:
            p_langs = ", ".join(json.loads(p_langs))
        except Exception:
            pass
    elif isinstance(p_langs, list):
        p_langs = ", ".join(p_langs)

    if isinstance(p_fws, str):
        try:
            p_fws = ", ".join(json.loads(p_fws))
        except Exception:
            pass
    elif isinstance(p_fws, list):
        p_fws = ", ".join(p_fws)

    lines.append(f"# Project: {p_name}")
    lines.append("")
    lines.append(p_desc)
    lines.append("")
    lines.append(f"**Languages:** {p_langs}")
    lines.append(f"**Frameworks:** {p_fws}")
    lines.append("")

    # Layers
    if layers:
        lines.append("## Relevant Layers")
        lines.append("")
        for layer in layers:
            lines.append(f"### {layer.get('name', '')}")
            lines.append(layer.get("description", ""))
            lines.append("")

    # Code components
    if nodes:
        lines.append("## Code Components")
        lines.append("")
        for node in nodes:
            n_name = node.get("name", "")
            n_type = node.get("type", "")
            lines.append(f"### {n_name} ({n_type})")
            if node.get("file_path"):
                lines.append(f"- **File:** {node['file_path']}")
            lines.append(f"- **Complexity:** {node.get('complexity', '')}")
            lines.append(f"- **Summary:** {node.get('summary', '')}")
            tags = node.get("tags", "")
            if tags:
                if isinstance(tags, str):
                    try:
                        tags = ", ".join(json.loads(tags))
                    except Exception:
                        pass
                elif isinstance(tags, list):
                    tags = ", ".join(tags)
                lines.append(f"- **Tags:** {tags}")
            if node.get("language_notes"):
                lines.append(f"- **Language Notes:** {node['language_notes']}")
            lines.append("")

    # Relationships
    if edges:
        node_map = {n.get("node_id", ""): n.get("name", "") for n in nodes}
        lines.append("## Relationships")
        lines.append("")
        for edge in edges:
            src_name = node_map.get(edge.get("source", ""), edge.get("source", ""))
            tgt_name = node_map.get(edge.get("target", ""), edge.get("target", ""))
            line = f"- {src_name} --[{edge.get('type', '')}]--> {tgt_name}"
            if edge.get("description"):
                line += f": {edge['description']}"
            lines.append(line)
        lines.append("")

    return "\n".join(lines)


def flatten_neptune_value(value):
    """Neptune valueMap returns lists for single values — flatten them."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def flatten_node(raw):
    """Convert Neptune valueMap result to flat dict."""
    flat = {}
    for key, value in raw.items():
        if key in ("id", "T.id"):
            flat["id"] = value
        elif key in ("label", "T.label"):
            flat["label"] = value
        else:
            flat[key] = flatten_neptune_value(value)
    return flat


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="clone_repository",
            description="Clone a public GitHub repository to local filesystem",
            inputSchema={
                "type": "object",
                "properties": {
                    "github_url": {"type": "string", "description": "GitHub repo URL"},
                    "project_id": {"type": "string", "description": "Unique project ID", "default": "default"},
                },
                "required": ["github_url"],
            },
        ),
        Tool(
            name="write_to_neptune",
            description="Write knowledge graph JSON (nodes + edges + layers + tour) to Neptune database. Call this after /understand generates knowledge-graph.json.",
            inputSchema={
                "type": "object",
                "properties": {
                    "graph_json_path": {"type": "string", "description": "Path to the knowledge-graph.json file"},
                    "project_id": {"type": "string", "description": "Project ID for multi-tenancy (e.g. repo name)"},
                },
                "required": ["graph_json_path", "project_id"],
            },
        ),
        Tool(
            name="query_neptune",
            description="Query Neptune graph database for code knowledge. Use build_chat_context for answering questions (searches nodes, expands 1-hop, returns formatted context). Other query types for specific lookups.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["build_chat_context", "get_all_nodes", "get_node_by_name",
                                 "get_node_with_edges", "get_node_neighbors", "get_nodes_by_type",
                                 "get_layers", "get_tour", "get_full_graph"],
                        "description": "Type of query. Use 'build_chat_context' to answer user questions (searches + 1-hop expansion + formatted output).",
                    },
                    "project_id": {"type": "string"},
                    "keyword": {"type": "string", "description": "Search keyword (for build_chat_context and get_node_by_name)"},
                    "node_id": {"type": "string", "description": "Node ID (for get_node_neighbors/get_node_with_edges)"},
                    "node_type": {"type": "string", "description": "Node type filter (for get_nodes_by_type)"},
                },
                "required": ["query_type", "project_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "clone_repository":
        return await handle_clone(arguments)
    elif name == "write_to_neptune":
        return await handle_write(arguments)
    elif name == "query_neptune":
        return await handle_query(arguments)
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def handle_clone(args: dict):
    github_url = args["github_url"]
    project_id = args.get("project_id", "default")
    local_path = os.path.join(CLONE_BASE_DIR, project_id)

    if os.path.exists(local_path):
        shutil.rmtree(local_path)
    os.makedirs(CLONE_BASE_DIR, exist_ok=True)

    result = subprocess.run(
        ["git", "clone", "--depth", "1", github_url, local_path],
        capture_output=True, text=True, timeout=120
    )

    if result.returncode != 0:
        return [TextContent(type="text", text=f"Clone failed: {result.stderr}")]

    file_count = sum(len(f) for _, _, f in os.walk(local_path) if ".git" not in _)
    return [TextContent(type="text", text=json.dumps({
        "status": "success", "local_path": local_path,
        "project_id": project_id, "file_count": file_count
    }))]


async def handle_write(args: dict):
    graph_path = args["graph_json_path"]
    project_id = args["project_id"]
    pid = escape_gremlin(project_id)

    if not os.path.exists(graph_path):
        return [TextContent(type="text", text=f"File not found: {graph_path}")]

    with open(graph_path) as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    layers = graph_data.get("layers", [])
    tour = graph_data.get("tour", [])
    project_meta = graph_data.get("project", {})
    version = graph_data.get("version", "1.0.0")
    kind = graph_data.get("kind", "codebase")

    if not NEPTUNE_ENDPOINT:
        output_path = f"/tmp/repos/{project_id}-graph-output.json"
        with open(output_path, "w") as f:
            json.dump(graph_data, f, indent=2)
        return [TextContent(type="text", text=json.dumps({
            "status": "success (local mode - Neptune unavailable)",
            "nodes_count": len(nodes), "edges_count": len(edges),
            "layers_count": len(layers), "tour_steps": len(tour),
            "saved_to": output_path
        }))]

    def _do_write():
        """Run entire write operation in a thread to avoid event loop conflict."""
        from gremlin_python.driver import client as gremlin_client
        endpoint = f"wss://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/gremlin"
        neptune = gremlin_client.Client(endpoint, "g")

        edges_written = 0
        try:
            # Drop existing data for this project
            neptune.submit(f"g.V().has('project_id', '{pid}').drop()").all().result()

            # Write project vertex
            proj_name = escape_gremlin(project_meta.get("name", project_id))
            proj_desc = escape_gremlin(project_meta.get("description", ""))
            proj_langs = escape_gremlin(json.dumps(project_meta.get("languages", [])))
            proj_fws = escape_gremlin(json.dumps(project_meta.get("frameworks", [])))
            proj_at = escape_gremlin(project_meta.get("analyzedAt", ""))
            proj_git = escape_gremlin(project_meta.get("gitCommitHash", ""))
            neptune.submit(
                f"g.addV('project').property(id, 'project:{pid}')"
                f".property('project_id', '{pid}')"
                f".property('name', '{proj_name}')"
                f".property('description', '{proj_desc}')"
                f".property('languages', '{proj_langs}')"
                f".property('frameworks', '{proj_fws}')"
                f".property('analyzedAt', '{proj_at}')"
                f".property('version', '{escape_gremlin(version)}')"
                f".property('kind', '{escape_gremlin(kind)}')"
                f".property('git_commit_hash', '{proj_git}')"
            ).all().result()

            # Write nodes with all fields
            for node in nodes:
                nid = escape_gremlin(node.get("id", ""))
                props = [
                    f".property(id, '{nid}')",
                    f".property('project_id', '{pid}')",
                    f".property('node_id', '{nid}')",
                    f".property('name', '{escape_gremlin(node.get('name', ''))}')",
                    f".property('type', '{escape_gremlin(node.get('type', ''))}')",
                    f".property('file_path', '{escape_gremlin(node.get('filePath', ''))}')",
                    f".property('summary', '{escape_gremlin(node.get('summary', ''))}')",
                    f".property('tags', '{escape_gremlin(json.dumps(node.get('tags', [])))}')",
                    f".property('complexity', '{escape_gremlin(node.get('complexity', ''))}')",
                ]
                line_range = node.get('lineRange')
                if line_range and len(line_range) == 2:
                    props.append(f".property('start_line', {line_range[0]})")
                    props.append(f".property('end_line', {line_range[1]})")
                if node.get('languageNotes'):
                    props.append(f".property('language_notes', '{escape_gremlin(node['languageNotes'])}')")
                if node.get('domainMeta'):
                    props.append(f".property('domain_meta', '{escape_gremlin(json.dumps(node['domainMeta']))}')")
                if node.get('knowledgeMeta'):
                    props.append(f".property('knowledge_meta', '{escape_gremlin(json.dumps(node['knowledgeMeta']))}')")

                neptune.submit(
                    "g.addV('code_entity')" + "".join(props) + ".iterate()"
                ).all().result()

            # Write edges using has('node_id', ...) for proper vertex lookup
            for edge in edges:
                src = escape_gremlin(edge.get("source", ""))
                tgt = escape_gremlin(edge.get("target", ""))
                etype = escape_gremlin(edge.get("type", "relates_to"))
                weight = edge.get("weight", 0.5)
                direction = escape_gremlin(edge.get("direction", "forward"))
                desc = escape_gremlin(edge.get("description", ""))
                try:
                    neptune.submit(
                        f"g.V().has('node_id', '{src}').as('src')"
                        f".V().has('node_id', '{tgt}').as('tgt')"
                        f".select('src').addE('{etype}').to(select('tgt'))"
                        f".property('project_id', '{pid}')"
                        f".property('weight', {weight})"
                        f".property('direction', '{direction}')"
                        f".property('description', '{desc}')"
                        f".iterate()"
                    ).all().result()
                    edges_written += 1
                except Exception:
                    pass

            # Write layers
            for layer in layers:
                lid = escape_gremlin(layer.get("id", ""))
                lname = escape_gremlin(layer.get("name", ""))
                ldesc = escape_gremlin(layer.get("description", ""))
                lnodes = escape_gremlin(json.dumps(layer.get("nodeIds", [])))
                neptune.submit(
                    f"g.addV('layer').property(id, '{lid}')"
                    f".property('project_id', '{pid}')"
                    f".property('name', '{lname}')"
                    f".property('description', '{ldesc}')"
                    f".property('nodeIds', '{lnodes}')"
                    f".iterate()"
                ).all().result()

            # Write tour steps
            for step in tour:
                order = step.get("order", 0)
                tid = f"tour:{pid}:{order}"
                ttitle = escape_gremlin(step.get("title", ""))
                tdesc = escape_gremlin(step.get("description", ""))
                tnodes = escape_gremlin(json.dumps(step.get("nodeIds", [])))
                tlesson = escape_gremlin(step.get("languageLesson", ""))
                neptune.submit(
                    f"g.addV('tour_step').property(id, '{tid}')"
                    f".property('project_id', '{pid}')"
                    f".property('order', {order})"
                    f".property('title', '{ttitle}')"
                    f".property('description', '{tdesc}')"
                    f".property('nodeIds', '{tnodes}')"
                    f".property('languageLesson', '{tlesson}')"
                    f".iterate()"
                ).all().result()

            return {
                "status": "success",
                "nodes_written": len(nodes),
                "edges_written": edges_written,
                "layers_written": len(layers),
                "tour_steps_written": len(tour),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
        finally:
            neptune.close()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_executor, _do_write)
    return [TextContent(type="text", text=json.dumps(result))]


async def handle_query(args: dict):
    project_id = args["project_id"]
    query_type = args["query_type"]
    pid = escape_gremlin(project_id)

    if not NEPTUNE_ENDPOINT:
        return await handle_query_local(args, project_id, query_type)

    try:
        if query_type == "build_chat_context":
            return await handle_build_chat_context(pid, args)

        elif query_type == "get_all_nodes":
            result = await gremlin_query(
                f"g.V().has('project_id', '{pid}').hasLabel('code_entity').valueMap(true).limit(200)"
            )

        elif query_type == "get_node_by_name":
            kw = escape_gremlin(args.get("keyword", ""))
            result = await gremlin_query(
                f"g.V().has('project_id', '{pid}').hasLabel('code_entity')"
                f".or(has('name', containing('{kw}')), has('summary', containing('{kw}')), has('tags', containing('{kw}')))"
                f".valueMap(true)"
            )

        elif query_type == "get_node_with_edges":
            nid = escape_gremlin(args.get("node_id", ""))
            results = await gremlin_queries([
                f"g.V().has('node_id', '{nid}').valueMap(true)",
                f"g.V().has('node_id', '{nid}').outE().project('target','type','weight','direction','description')"
                f".by(inV().values('name'))"
                f".by(label())"
                f".by(coalesce(values('weight'), constant(0.5)))"
                f".by(coalesce(values('direction'), constant('forward')))"
                f".by(coalesce(values('description'), constant('')))",
                f"g.V().has('node_id', '{nid}').inE().project('source','type','weight','direction','description')"
                f".by(outV().values('name'))"
                f".by(label())"
                f".by(coalesce(values('weight'), constant(0.5)))"
                f".by(coalesce(values('direction'), constant('forward')))"
                f".by(coalesce(values('description'), constant('')))",
            ])
            node_result, out_edges, in_edges = results
            result = {
                "node": flatten_node(node_result[0]) if node_result else None,
                "outgoing_edges": out_edges,
                "incoming_edges": in_edges,
            }
            return [TextContent(type="text", text=json.dumps(result, default=str))]

        elif query_type == "get_node_neighbors":
            nid = escape_gremlin(args.get("node_id", ""))
            result = await gremlin_query(
                f"g.V().has('node_id', '{nid}').both().valueMap(true).limit(50)"
            )

        elif query_type == "get_nodes_by_type":
            nt = escape_gremlin(args.get("node_type", ""))
            result = await gremlin_query(
                f"g.V().has('project_id', '{pid}').has('type', '{nt}').valueMap(true)"
            )

        elif query_type == "get_layers":
            result = await gremlin_query(
                f"g.V().hasLabel('layer').has('project_id', '{pid}').valueMap(true).order().by('name')"
            )

        elif query_type == "get_tour":
            result = await gremlin_query(
                f"g.V().hasLabel('tour_step').has('project_id', '{pid}').valueMap(true).order().by('order')"
            )

        elif query_type == "get_full_graph":
            results = await gremlin_queries([
                f"g.V().has('project_id', '{pid}').hasLabel('code_entity').valueMap(true).limit(500)",
                f"g.E().has('project_id', '{pid}').project('source','target','type','weight','direction','description')"
                f".by(outV().values('node_id'))"
                f".by(inV().values('node_id'))"
                f".by(label())"
                f".by(coalesce(values('weight'), constant(0.5)))"
                f".by(coalesce(values('direction'), constant('forward')))"
                f".by(coalesce(values('description'), constant('')))",
                f"g.V().hasLabel('layer').has('project_id', '{pid}').valueMap(true)",
                f"g.V().hasLabel('tour_step').has('project_id', '{pid}').valueMap(true).order().by('order')",
            ])
            nodes, edges, layers, tour = results
            return [TextContent(type="text", text=json.dumps({
                "nodes": [flatten_node(n) for n in nodes],
                "edges": edges,
                "layers": [flatten_node(l) for l in layers],
                "tour": [flatten_node(t) for t in tour]
            }, default=str))]
        else:
            result = await gremlin_query(
                f"g.V().has('project_id', '{pid}').valueMap(true).limit(50)"
            )

        # Flatten results
        if isinstance(result, list):
            flattened = [flatten_node(r) for r in result]
            return [TextContent(type="text", text=json.dumps(flattened[:50], default=str))]
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def handle_build_chat_context(pid, args):
    """
    Replicate /understand-chat's buildChatContext() logic:
    1. Multi-field search for keyword → matched nodes (limit 15)
    2. 1-hop expansion via edges (both directions)
    3. Filter edges where both endpoints in expanded set
    4. Filter layers containing expanded nodes
    5. Format as markdown
    """
    keyword = escape_gremlin(args.get("keyword", ""))

    if not keyword:
        return [TextContent(type="text", text=json.dumps({"error": "keyword is required for build_chat_context"}))]

    # Step 1: Multi-field search (name + summary + tags)
    matched_raw = await gremlin_query(
        f"g.V().has('project_id', '{pid}').hasLabel('code_entity')"
        f".or(has('name', containing('{keyword}')), has('summary', containing('{keyword}')), has('tags', containing('{keyword}')))"
        f".valueMap(true).limit(15)"
    )

    matched_nodes = [flatten_node(r) for r in matched_raw]
    matched_ids = set(n.get("node_id", "") for n in matched_nodes)

    if not matched_ids:
        return [TextContent(type="text", text=json.dumps({
            "status": "no_results",
            "message": f"No nodes found matching '{keyword}'. Try different keywords.",
            "formatted_context": ""
        }))]

    # Step 2: 1-hop expansion via edges (both directions) — batch query
    neighbor_queries = [
        f"g.V().has('node_id', '{escape_gremlin(nid)}').both().values('node_id')"
        for nid in matched_ids
    ]
    neighbor_results = await gremlin_queries(neighbor_queries)

    expanded_ids = set(matched_ids)
    for neighbors in neighbor_results:
        expanded_ids.update(neighbors)

    # Fetch expanded nodes that weren't in the initial match — batch query
    extra_ids = expanded_ids - matched_ids
    expanded_nodes = list(matched_nodes)
    if extra_ids:
        extra_queries = [
            f"g.V().has('node_id', '{escape_gremlin(nid)}').valueMap(true)"
            for nid in extra_ids
        ]
        extra_results = await gremlin_queries(extra_queries)
        for raw in extra_results:
            if raw:
                expanded_nodes.append(flatten_node(raw[0]))

    # Step 3: Get edges where both endpoints are in expanded set
    all_edges = await gremlin_query(
        f"g.E().has('project_id', '{pid}')"
        f".project('source','target','type','weight','direction','description')"
        f".by(outV().values('node_id'))"
        f".by(inV().values('node_id'))"
        f".by(label())"
        f".by(coalesce(values('weight'), constant(0.5)))"
        f".by(coalesce(values('direction'), constant('forward')))"
        f".by(coalesce(values('description'), constant('')))"
    )

    relevant_edges = [
        e for e in all_edges
        if e.get("source") in expanded_ids and e.get("target") in expanded_ids
    ]

    # Step 4: Get layers and project metadata — batch query
    meta_results = await gremlin_queries([
        f"g.V().hasLabel('layer').has('project_id', '{pid}').valueMap(true)",
        f"g.V().hasLabel('project').has('project_id', '{pid}').valueMap(true)",
    ])
    all_layers_raw, project_raw = meta_results

    all_layers = [flatten_node(l) for l in all_layers_raw]
    relevant_layers = []
    for layer in all_layers:
        node_ids_str = layer.get("nodeIds", "[]")
        try:
            layer_node_ids = json.loads(node_ids_str) if isinstance(node_ids_str, str) else node_ids_str
        except Exception:
            layer_node_ids = []
        if any(nid in expanded_ids for nid in layer_node_ids):
            relevant_layers.append(layer)

    # Step 5: Format as markdown (matching /understand-chat output)
    project = flatten_node(project_raw[0]) if project_raw else {}
    formatted = format_context_markdown(project, expanded_nodes, relevant_edges, relevant_layers)

    return [TextContent(type="text", text=json.dumps({
        "status": "success",
        "matched_count": len(matched_nodes),
        "expanded_count": len(expanded_nodes),
        "edges_count": len(relevant_edges),
        "layers_count": len(relevant_layers),
        "formatted_context": formatted
    }, default=str))]


async def handle_query_local(args, project_id, query_type):
    """Handle queries in local mode (no Neptune) using saved JSON."""
    output_path = f"/tmp/repos/{project_id}-graph-output.json"
    if not os.path.exists(output_path):
        return [TextContent(type="text", text="No graph data found locally")]

    with open(output_path) as f:
        data = json.load(f)

    if query_type == "build_chat_context":
        keyword = args.get("keyword", "").lower()
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        layers = data.get("layers", [])
        project = data.get("project", {})

        # Step 1: multi-field search
        matched = [
            n for n in nodes
            if keyword in n.get("name", "").lower()
            or keyword in n.get("summary", "").lower()
            or keyword in json.dumps(n.get("tags", [])).lower()
        ][:15]
        matched_ids = set(n["id"] for n in matched)

        # Step 2: 1-hop expansion
        expanded_ids = set(matched_ids)
        for edge in edges:
            if edge["source"] in matched_ids:
                expanded_ids.add(edge["target"])
            if edge["target"] in matched_ids:
                expanded_ids.add(edge["source"])

        node_map = {n["id"]: n for n in nodes}
        expanded_nodes = [node_map[nid] for nid in expanded_ids if nid in node_map]

        # Step 3: edges where both endpoints in expanded set
        relevant_edges = [
            e for e in edges
            if e["source"] in expanded_ids and e["target"] in expanded_ids
        ]

        # Step 4: layers containing expanded nodes
        relevant_layers = [
            l for l in layers
            if any(nid in expanded_ids for nid in l.get("nodeIds", []))
        ]

        # Convert nodes to flat format for formatter
        flat_nodes = []
        for n in expanded_nodes:
            flat_nodes.append({
                "node_id": n.get("id", ""),
                "name": n.get("name", ""),
                "type": n.get("type", ""),
                "file_path": n.get("filePath", ""),
                "summary": n.get("summary", ""),
                "tags": json.dumps(n.get("tags", [])),
                "complexity": n.get("complexity", ""),
                "language_notes": n.get("languageNotes", ""),
            })

        formatted = format_context_markdown(project, flat_nodes, relevant_edges, relevant_layers)
        return [TextContent(type="text", text=json.dumps({
            "status": "success",
            "matched_count": len(matched),
            "expanded_count": len(expanded_nodes),
            "edges_count": len(relevant_edges),
            "layers_count": len(relevant_layers),
            "formatted_context": formatted
        }))]

    elif query_type == "get_node_by_name":
        keyword = args.get("keyword", "").lower()
        nodes = [n for n in data.get("nodes", []) if keyword in n.get("name", "").lower()
                 or keyword in n.get("summary", "").lower()]
        return [TextContent(type="text", text=json.dumps(nodes[:20]))]

    elif query_type == "get_nodes_by_type":
        nt = args.get("node_type", "")
        nodes = [n for n in data.get("nodes", []) if n.get("type") == nt]
        return [TextContent(type="text", text=json.dumps(nodes))]

    elif query_type == "get_layers":
        return [TextContent(type="text", text=json.dumps(data.get("layers", [])))]

    elif query_type == "get_tour":
        return [TextContent(type="text", text=json.dumps(data.get("tour", [])))]

    elif query_type == "get_full_graph":
        return [TextContent(type="text", text=json.dumps({
            "nodes": data.get("nodes", []),
            "edges": data.get("edges", []),
            "layers": data.get("layers", []),
            "tour": data.get("tour", [])
        }))]

    else:
        return [TextContent(type="text", text=json.dumps({
            "nodes": len(data.get("nodes", [])),
            "edges": len(data.get("edges", []))
        }))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
