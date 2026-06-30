"""
Neptune Writer — writes knowledge-graph.json to Neptune via HTTP POST + SigV4.

Designed to be imported directly (no subprocess needed). Each node/edge is
written individually so failures are isolated and reported precisely.

Usage as library:
    from neptune_writer import write_graph
    result = write_graph(graph_path, project_id, progress_callback=None)

Usage as standalone (backward compat):
    python3 neptune_writer.py <graph_json_path> <project_id>
"""

import json
import logging
import os
import sys
from typing import Callable

from neptune_http import NeptuneHttpClient, escape_gremlin

logger = logging.getLogger("neptune_writer")


def _build_node_query(node: dict, pid: str) -> str:
    """Build addV Gremlin query for a single code_entity node."""
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
        f".property('complexity', '{escape_gremlin(node.get('complexity', 'moderate'))}')",
    ]

    line_range = node.get("lineRange")
    if line_range and len(line_range) == 2:
        props.append(f".property('start_line', {line_range[0]})")
        props.append(f".property('end_line', {line_range[1]})")
    if node.get("languageNotes"):
        props.append(f".property('language_notes', '{escape_gremlin(node['languageNotes'])}')")
    if node.get("domainMeta"):
        props.append(f".property('domain_meta', '{escape_gremlin(json.dumps(node['domainMeta']))}')")
    if node.get("knowledgeMeta"):
        props.append(f".property('knowledge_meta', '{escape_gremlin(json.dumps(node['knowledgeMeta']))}')")

    return "g.addV('code_entity')" + "".join(props) + ".iterate()"


def _build_edge_query(edge: dict, pid: str) -> str:
    """Build addE Gremlin query for a single edge."""
    src = escape_gremlin(edge.get("source", ""))
    tgt = escape_gremlin(edge.get("target", ""))
    etype = escape_gremlin(edge.get("type", "relates_to"))
    weight = edge.get("weight", 0.5)
    direction = escape_gremlin(edge.get("direction", "forward"))
    desc = escape_gremlin(edge.get("description", ""))

    return (
        f"g.V().has('node_id', '{src}').as('src')"
        f".V().has('node_id', '{tgt}').as('tgt')"
        f".select('src').addE('{etype}').to(select('tgt'))"
        f".property('project_id', '{pid}')"
        f".property('weight', {weight})"
        f".property('direction', '{direction}')"
        f".property('description', '{desc}')"
        f".iterate()"
    )


def write_graph(
    graph_path: str,
    project_id: str,
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    """Write knowledge-graph.json to Neptune, one statement at a time.

    Args:
        graph_path: Path to knowledge-graph.json file.
        project_id: Project ID for multi-tenancy.
        progress_callback: Optional function called with progress messages.

    Returns:
        Dict with status, counts, and failure details.
    """
    neptune_endpoint = os.environ.get("NEPTUNE_ENDPOINT", "")
    print(f"[neptune_writer] NEPTUNE_ENDPOINT={neptune_endpoint!r}, project_id={project_id!r}, graph_path={graph_path!r}", flush=True)
    if not neptune_endpoint:
        return {"status": "error", "error": "NEPTUNE_ENDPOINT not set"}

    with open(graph_path) as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    layers = graph_data.get("layers", [])
    tour = graph_data.get("tour", [])
    project_meta = graph_data.get("project", {})
    version = graph_data.get("version", "1.0.0")
    kind = graph_data.get("kind", "codebase")
    pid = escape_gremlin(project_id)
    print(f"[neptune_writer] nodes={len(nodes)}, edges={len(edges)}, layers={len(layers)}", flush=True)

    client = NeptuneHttpClient(endpoint=neptune_endpoint)

    nodes_success = 0
    nodes_failed = 0
    edges_success = 0
    edges_failed = 0
    failed_nodes = []
    failed_edges = []

    def _progress(msg: str):
        print(f"[neptune_writer] {msg}", flush=True)
        if progress_callback:
            progress_callback(msg)

    try:
        # Step 1: Drop existing project data
        _progress("Clearing existing data...")
        client.execute(f"g.V().has('project_id', '{pid}').drop()")
        try:
            client.execute(f"g.V('project:{pid}').drop()")
        except Exception:
            pass

        # Step 2: Write project vertex
        pname = escape_gremlin(project_meta.get("name", project_id))
        pdesc = escape_gremlin(project_meta.get("description", ""))
        plangs = escape_gremlin(json.dumps(project_meta.get("languages", [])))
        pfws = escape_gremlin(json.dumps(project_meta.get("frameworks", [])))
        pat = escape_gremlin(project_meta.get("analyzedAt", ""))
        pgit = escape_gremlin(project_meta.get("gitCommitHash", ""))

        client.execute(
            f"g.addV('project').property(id, 'project:{pid}')"
            f".property('project_id', '{pid}')"
            f".property('name', '{pname}')"
            f".property('description', '{pdesc}')"
            f".property('languages', '{plangs}')"
            f".property('frameworks', '{pfws}')"
            f".property('analyzedAt', '{pat}')"
            f".property('version', '{escape_gremlin(version)}')"
            f".property('kind', '{escape_gremlin(kind)}')"
            f".property('git_commit_hash', '{pgit}')"
        )

        # Step 3: Write nodes one by one
        total_nodes = len(nodes)
        _progress(f"Writing {total_nodes} nodes...")
        for i, node in enumerate(nodes):
            try:
                query = _build_node_query(node, pid)
                client.execute(query)
                nodes_success += 1
            except Exception as e:
                nodes_failed += 1
                if nodes_failed <= 3:
                    print(f"[neptune_writer] Node write FAILED [{node.get('id','')}]: {type(e).__name__}: {e}", flush=True)
                if len(failed_nodes) < 10:
                    failed_nodes.append({
                        "id": node.get("id", ""),
                        "name": node.get("name", ""),
                        "error": str(e)[:200],
                    })

            if (i + 1) % 10 == 0:
                _progress(f"Nodes: {i + 1}/{total_nodes} (ok={nodes_success}, fail={nodes_failed})")

        _progress(f"Nodes complete: {nodes_success}/{total_nodes}")

        # Step 4: Write edges one by one
        total_edges = len(edges)
        _progress(f"Writing {total_edges} edges...")
        for i, edge in enumerate(edges):
            try:
                query = _build_edge_query(edge, pid)
                client.execute(query)
                edges_success += 1
            except Exception as e:
                edges_failed += 1
                if len(failed_edges) < 10:
                    failed_edges.append({
                        "source": edge.get("source", ""),
                        "target": edge.get("target", ""),
                        "type": edge.get("type", ""),
                        "error": str(e)[:200],
                    })

            if (i + 1) % 10 == 0:
                _progress(f"Edges: {i + 1}/{total_edges} (ok={edges_success}, fail={edges_failed})")

        _progress(f"Edges complete: {edges_success}/{total_edges}")

        # Step 5: Write layers
        for layer in layers:
            lid = escape_gremlin(layer.get("id", ""))
            try:
                client.execute(
                    f"g.addV('layer').property(id, '{lid}')"
                    f".property('project_id', '{pid}')"
                    f".property('name', '{escape_gremlin(layer.get('name', ''))}')"
                    f".property('description', '{escape_gremlin(layer.get('description', ''))}')"
                    f".property('nodeIds', '{escape_gremlin(json.dumps(layer.get('nodeIds', [])))}')"
                    f".iterate()"
                )
            except Exception:
                pass

        # Step 6: Write tour steps
        for step in tour:
            order = step.get("order", 0)
            tid = f"tour:{pid}:{order}"
            try:
                client.execute(
                    f"g.addV('tour_step').property(id, '{tid}')"
                    f".property('project_id', '{pid}')"
                    f".property('order', {order})"
                    f".property('title', '{escape_gremlin(step.get('title', ''))}')"
                    f".property('description', '{escape_gremlin(step.get('description', ''))}')"
                    f".property('nodeIds', '{escape_gremlin(json.dumps(step.get('nodeIds', [])))}')"
                    f".property('languageLesson', '{escape_gremlin(step.get('languageLesson', ''))}')"
                    f".iterate()"
                )
            except Exception:
                pass

        # Step 7: Verify write
        _progress("Verifying write...")
        verified_count = client.count_vertices(project_id, "code_entity")
        _progress(f"Verified: {verified_count} code_entity nodes in Neptune")

        # Determine status
        if nodes_failed == 0 and edges_failed == 0 and verified_count == nodes_success:
            status = "success"
        elif verified_count > 0 and nodes_success > total_nodes * 0.8:
            status = "partial"
        else:
            status = "error"

        result = {
            "status": status,
            "nodes_attempted": total_nodes,
            "nodes_written": nodes_success,
            "nodes_failed": nodes_failed,
            "edges_attempted": total_edges,
            "edges_written": edges_success,
            "edges_failed": edges_failed,
            "layers_written": len(layers),
            "tour_steps_written": len(tour),
            "verified_node_count": verified_count,
        }

        if failed_nodes:
            result["failed_nodes"] = failed_nodes
        if failed_edges:
            result["failed_edges"] = failed_edges

        return result

    except Exception as e:
        import traceback
        print(f"[neptune_writer] FATAL ERROR: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "nodes_written": nodes_success,
            "nodes_failed": nodes_failed,
            "edges_written": edges_success,
            "edges_failed": edges_failed,
        }


def delete_graph(project_id: str) -> dict:
    """Delete all vertices and edges for a project from Neptune.

    Returns {"status": "success", "verified": True} or {"status": "error", ...}.
    """
    neptune_endpoint = os.environ.get("NEPTUNE_ENDPOINT", "")
    if not neptune_endpoint:
        return {"status": "error", "error": "NEPTUNE_ENDPOINT not set"}

    pid = escape_gremlin(project_id)
    client = NeptuneHttpClient(endpoint=neptune_endpoint)

    try:
        client.execute(f"g.V().has('project_id', '{pid}').drop()")
        try:
            client.execute(f"g.V('project:{pid}').drop()")
        except Exception:
            pass

        remaining = client.count_vertices(project_id, "code_entity")
        if remaining == 0:
            return {"status": "success", "verified": True, "project_id": project_id}
        else:
            return {"status": "partial", "remaining_nodes": remaining, "project_id": project_id}
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


# Backward-compatible CLI entry point
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) != 3:
        print(json.dumps({"status": "error", "error": "Usage: neptune_writer.py <graph_path> <project_id>"}))
        sys.exit(1)

    result = write_graph(sys.argv[1], sys.argv[2])
    print(json.dumps(result))
    sys.exit(0 if result.get("status") in ("success", "partial") else 1)
