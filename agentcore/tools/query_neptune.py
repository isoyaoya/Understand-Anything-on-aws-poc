"""
Custom Tool: query_neptune
Use fixed Gremlin templates to query Neptune (no LLM-generated Gremlin).
Supports build_chat_context for /understand-chat parity.
"""

from gremlin_python.driver import client as gremlin_client
import json
import os

NEPTUNE_ENDPOINT = os.environ.get("NEPTUNE_ENDPOINT", "")
NEPTUNE_PORT = os.environ.get("NEPTUNE_PORT", "8182")


def get_neptune_client():
    endpoint = f"wss://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/gremlin"
    return gremlin_client.Client(endpoint, 'g')


def escape_gremlin(s: str) -> str:
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


def flatten_neptune_value(value):
    """Neptune valueMap returns lists for single values — flatten them."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def flatten_node(raw: dict) -> dict:
    """Convert Neptune valueMap result to flat dict."""
    flat = {}
    for key, value in raw.items():
        if key in ('id', 'T.id'):
            flat['id'] = value
        elif key in ('label', 'T.label'):
            flat['label'] = value
        else:
            flat[key] = flatten_neptune_value(value)

    # Deserialize JSON string fields
    for field in ('tags', 'metadata', 'nodeIds', 'domain_meta', 'knowledge_meta'):
        if field in flat and isinstance(flat[field], str):
            try:
                flat[field] = json.loads(flat[field])
            except Exception:
                pass

    return flat


async def query_neptune(query_type: str, project_id: str, **kwargs) -> dict:
    """
    Query Neptune using predefined templates.

    Args:
        query_type: One of "build_chat_context", "get_all_nodes", "get_all_edges",
                    "get_node_by_name", "get_node_neighbors", "get_nodes_by_type",
                    "get_full_graph"
        project_id: Project identifier
        **kwargs: Additional params (keyword, node_id, node_type)
    """
    pid = escape_gremlin(project_id)
    neptune = get_neptune_client()

    try:
        if query_type == "build_chat_context":
            return await _build_chat_context(neptune, pid, kwargs.get("keyword", ""))

        elif query_type == "get_all_nodes":
            result = neptune.submit(
                f"g.V().has('project_id', '{pid}').hasLabel('code_entity').valueMap(true).limit(2000)"
            ).all().result()

        elif query_type == "get_all_edges":
            result = neptune.submit(
                f"g.E().has('project_id', '{pid}').project('source','target','type','weight','direction','description')"
                f".by(outV().values('node_id'))"
                f".by(inV().values('node_id'))"
                f".by(label())"
                f".by(coalesce(values('weight'), constant(0.5)))"
                f".by(coalesce(values('direction'), constant('forward')))"
                f".by(coalesce(values('description'), constant('')))"
                f".limit(5000)"
            ).all().result()
            return {"status": "success", "results": result, "count": len(result)}

        elif query_type == "get_node_by_name":
            kw = escape_gremlin(kwargs.get("keyword", ""))
            result = neptune.submit(
                f"g.V().has('project_id', '{pid}').hasLabel('code_entity')"
                f".or(has('name', containing('{kw}')), has('summary', containing('{kw}')), has('tags', containing('{kw}')))"
                f".valueMap(true)"
            ).all().result()

        elif query_type == "get_node_neighbors":
            nid = escape_gremlin(kwargs.get("node_id", ""))
            result = neptune.submit(
                f"g.V().has('node_id', '{nid}').both().valueMap(true).limit(50)"
            ).all().result()

        elif query_type == "get_nodes_by_type":
            nt = escape_gremlin(kwargs.get("node_type", ""))
            result = neptune.submit(
                f"g.V().has('project_id', '{pid}').has('type', '{nt}').valueMap(true)"
            ).all().result()

        elif query_type == "get_full_graph":
            nodes = neptune.submit(
                f"g.V().has('project_id', '{pid}').hasLabel('code_entity').valueMap(true).limit(500)"
            ).all().result()
            edges = neptune.submit(
                f"g.E().has('project_id', '{pid}').project('source','target','type','weight','direction','description')"
                f".by(outV().values('node_id'))"
                f".by(inV().values('node_id'))"
                f".by(label())"
                f".by(coalesce(values('weight'), constant(0.5)))"
                f".by(coalesce(values('direction'), constant('forward')))"
                f".by(coalesce(values('description'), constant('')))"
            ).all().result()
            return {
                "status": "success",
                "nodes": [flatten_node(n) for n in nodes],
                "edges": edges,
                "count": len(nodes)
            }
        else:
            return {"status": "error", "error": f"Unknown query_type: {query_type}"}

        converted = [flatten_node(r) for r in result]
        return {"status": "success", "results": converted, "count": len(converted), "query_type": query_type}

    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        neptune.close()


async def _build_chat_context(neptune, pid: str, keyword: str) -> dict:
    """
    Replicate /understand-chat's buildChatContext() logic:
    1. Multi-field search → matched nodes (limit 15)
    2. 1-hop expansion via edges (both directions)
    3. Edges where both endpoints in expanded set
    4. Layers containing expanded nodes
    5. Project metadata
    """
    kw = escape_gremlin(keyword)

    if not kw:
        return {"status": "error", "error": "keyword is required for build_chat_context"}

    # Step 1: Multi-field search
    matched_raw = neptune.submit(
        f"g.V().has('project_id', '{pid}').hasLabel('code_entity')"
        f".or(has('name', containing('{kw}')), has('summary', containing('{kw}')), has('tags', containing('{kw}')))"
        f".valueMap(true).limit(15)"
    ).all().result()

    matched_nodes = [flatten_node(r) for r in matched_raw]
    matched_ids = set(n.get("node_id", "") for n in matched_nodes)

    if not matched_ids:
        return {"status": "no_results", "message": f"No nodes found matching '{keyword}'."}

    # Step 2: 1-hop expansion
    expanded_ids = set(matched_ids)
    for nid in list(matched_ids):
        nid_esc = escape_gremlin(nid)
        neighbors = neptune.submit(
            f"g.V().has('node_id', '{nid_esc}').both().values('node_id')"
        ).all().result()
        expanded_ids.update(neighbors)

    # Fetch expanded nodes
    extra_ids = expanded_ids - matched_ids
    expanded_nodes = list(matched_nodes)
    for nid in extra_ids:
        nid_esc = escape_gremlin(nid)
        raw = neptune.submit(f"g.V().has('node_id', '{nid_esc}').valueMap(true)").all().result()
        if raw:
            expanded_nodes.append(flatten_node(raw[0]))

    # Step 3: Edges where both endpoints in expanded set
    all_edges = neptune.submit(
        f"g.E().has('project_id', '{pid}')"
        f".project('source','target','type','weight','direction','description')"
        f".by(outV().values('node_id'))"
        f".by(inV().values('node_id'))"
        f".by(label())"
        f".by(coalesce(values('weight'), constant(0.5)))"
        f".by(coalesce(values('direction'), constant('forward')))"
        f".by(coalesce(values('description'), constant('')))"
    ).all().result()
    relevant_edges = [e for e in all_edges if e.get("source") in expanded_ids and e.get("target") in expanded_ids]

    # Step 4: Relevant layers
    all_layers_raw = neptune.submit(
        f"g.V().hasLabel('layer').has('project_id', '{pid}').valueMap(true)"
    ).all().result()
    all_layers = [flatten_node(l) for l in all_layers_raw]
    relevant_layers = []
    for layer in all_layers:
        node_ids = layer.get("nodeIds", [])
        if isinstance(node_ids, str):
            try:
                node_ids = json.loads(node_ids)
            except Exception:
                node_ids = []
        if any(nid in expanded_ids for nid in node_ids):
            relevant_layers.append(layer)

    # Step 5: Project metadata
    project_raw = neptune.submit(
        f"g.V().hasLabel('project').has('project_id', '{pid}').valueMap(true)"
    ).all().result()
    project = flatten_node(project_raw[0]) if project_raw else {}

    return {
        "status": "success",
        "project": project,
        "nodes": expanded_nodes,
        "edges": relevant_edges,
        "layers": relevant_layers,
        "matched_count": len(matched_nodes),
        "expanded_count": len(expanded_nodes),
    }
