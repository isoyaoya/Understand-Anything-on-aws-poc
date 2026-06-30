"""S3 manager for storing and retrieving knowledge graph JSON files."""

import json
import os
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

S3_KNOWLEDGE_BUCKET = os.environ.get("S3_KNOWLEDGE_BUCKET", "")


def _get_s3_client():
    """Return a boto3 S3 client."""
    return boto3.client("s3")


def extract_project_id(github_url: str) -> str:
    """Extract project_id from GitHub URL.

    https://github.com/facebook/react -> 'facebook-react'
    https://github.com/vercel/next.js -> 'vercel-next.js'
    """
    url = github_url.rstrip("/")
    parts = url.split("/")
    if len(parts) >= 2:
        org = parts[-2]
        repo = parts[-1].removesuffix(".git")
        return f"{org}-{repo}"
    return ""


def upload_graph(project_id: str, json_path: str, github_url: str = "") -> dict:
    """Upload knowledge-graph.json to S3 and update projects.json index.

    Returns {"status": "success", "s3_key": "...", "project_id": "..."} on success,
    or {"status": "error", "message": "..."} on failure.
    """
    if not S3_KNOWLEDGE_BUCKET:
        return {"status": "error", "message": "S3_KNOWLEDGE_BUCKET not configured"}

    s3_key = f"{project_id}/knowledge-graph.json"

    try:
        s3 = _get_s3_client()
        s3.upload_file(json_path, S3_KNOWLEDGE_BUCKET, s3_key)
    except (ClientError, Exception) as e:
        return {"status": "error", "message": str(e)}

    # Read the JSON to extract project metadata for the index
    metadata = {
        "name": project_id,
        "org": "",
        "github_url": github_url,
        "analyzed_at": datetime.utcnow().isoformat() + "Z",
        "languages": [],
        "frameworks": [],
    }

    try:
        with open(json_path, "r") as f:
            graph_data = json.load(f)

        project_info = graph_data.get("project", {})
        if project_info.get("name"):
            metadata["name"] = project_info["name"]
        if project_info.get("languages"):
            metadata["languages"] = project_info["languages"]
        if project_info.get("frameworks"):
            metadata["frameworks"] = project_info["frameworks"]
    except (json.JSONDecodeError, IOError):
        pass

    # Extract org from github_url if available
    if github_url:
        parts = github_url.rstrip("/").split("/")
        if len(parts) >= 2:
            metadata["org"] = parts[-2]

    try:
        update_projects_index(project_id, metadata)
    except Exception:
        pass

    return {"status": "success", "s3_key": s3_key, "project_id": project_id}


def update_projects_index(project_id: str, metadata: dict) -> None:
    """Read existing projects.json from S3, add/update this project entry, write back.

    metadata should contain: name, org, github_url, analyzed_at, languages, frameworks.
    If projects.json doesn't exist yet, create it.
    """
    if not S3_KNOWLEDGE_BUCKET:
        return

    s3 = _get_s3_client()
    projects = []

    # Try to read existing projects.json
    try:
        response = s3.get_object(Bucket=S3_KNOWLEDGE_BUCKET, Key="projects.json")
        body = response["Body"].read().decode("utf-8")
        data = json.loads(body)
        projects = data.get("projects", []) if isinstance(data, dict) else data
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            projects = []
        else:
            raise
    except (json.JSONDecodeError, Exception):
        projects = []

    # Update or add the project entry
    entry = {"project_id": project_id, **metadata}
    found = False
    for i, proj in enumerate(projects):
        if proj.get("project_id") == project_id:
            projects[i] = entry
            found = True
            break

    if not found:
        projects.append(entry)

    # Write back with wrapper format
    s3.put_object(
        Bucket=S3_KNOWLEDGE_BUCKET,
        Key="projects.json",
        Body=json.dumps({"projects": projects}, indent=2),
        ContentType="application/json",
    )


def list_projects() -> list:
    """Read projects.json from S3, return list of project entries.

    If projects.json doesn't exist or bucket not configured, return [].
    """
    if not S3_KNOWLEDGE_BUCKET:
        return []

    try:
        s3 = _get_s3_client()
        response = s3.get_object(Bucket=S3_KNOWLEDGE_BUCKET, Key="projects.json")
        body = response["Body"].read().decode("utf-8")
        data = json.loads(body)
        if isinstance(data, dict):
            return data.get("projects", [])
        return data
    except (ClientError, json.JSONDecodeError, Exception):
        return []


def delete_project(project_id: str) -> dict:
    """Delete a project's data from S3 and remove it from the projects index.

    Deletes {project_id}/knowledge-graph.json and removes the entry from projects.json.
    Returns {"status": "success", "deleted_keys": [...]} or {"status": "error", ...}.
    """
    if not S3_KNOWLEDGE_BUCKET:
        return {"status": "error", "message": "S3_KNOWLEDGE_BUCKET not configured"}

    s3 = _get_s3_client()
    deleted_keys = []

    # Delete the knowledge graph JSON
    s3_key = f"{project_id}/knowledge-graph.json"
    try:
        s3.delete_object(Bucket=S3_KNOWLEDGE_BUCKET, Key=s3_key)
        deleted_keys.append(s3_key)
    except ClientError as e:
        return {"status": "error", "message": f"Failed to delete {s3_key}: {e}"}

    # Remove from projects.json index
    try:
        response = s3.get_object(Bucket=S3_KNOWLEDGE_BUCKET, Key="projects.json")
        body = response["Body"].read().decode("utf-8")
        data = json.loads(body)
        projects = data.get("projects", []) if isinstance(data, dict) else data
        projects = [p for p in projects if p.get("project_id") != project_id]
        s3.put_object(
            Bucket=S3_KNOWLEDGE_BUCKET,
            Key="projects.json",
            Body=json.dumps({"projects": projects}, indent=2),
            ContentType="application/json",
        )
    except (ClientError, json.JSONDecodeError, Exception) as e:
        return {"status": "partial", "message": f"Graph deleted but index update failed: {e}", "deleted_keys": deleted_keys}

    return {"status": "success", "deleted_keys": deleted_keys, "project_id": project_id}


def download_graph(project_id: str, local_path: str) -> str:
    """Download knowledge-graph.json from S3 to local_path.

    Returns local file path on success, or empty string on failure.
    """
    if not S3_KNOWLEDGE_BUCKET:
        return ""

    s3_key = f"{project_id}/knowledge-graph.json"

    try:
        s3 = _get_s3_client()
        s3.download_file(S3_KNOWLEDGE_BUCKET, s3_key, local_path)
        return local_path
    except (ClientError, Exception):
        return ""
