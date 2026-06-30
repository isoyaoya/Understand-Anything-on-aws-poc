"""
Custom Tool: clone_repository
Clone GitHub repo to container local filesystem (not S3)
"""

import subprocess
import os
import shutil

CLONE_BASE_DIR = "/tmp/repos"


async def clone_repository(github_url: str, project_id: str = "default") -> dict:
    """
    Clone a public GitHub repository to local filesystem.

    Args:
        github_url: Public GitHub repository URL
        project_id: Unique project identifier

    Returns:
        {
            "status": "success",
            "local_path": "/tmp/repos/<project_id>",
            "project_id": "<project_id>"
        }
    """

    local_path = os.path.join(CLONE_BASE_DIR, project_id)

    # Clean existing directory
    if os.path.exists(local_path):
        shutil.rmtree(local_path)

    os.makedirs(CLONE_BASE_DIR, exist_ok=True)

    # Shallow clone (fast, saves space)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", github_url, local_path],
        capture_output=True, text=True, timeout=120
    )

    if result.returncode != 0:
        return {
            "status": "error",
            "error": f"Clone failed: {result.stderr}"
        }

    # Count files (excluding .git)
    file_count = 0
    for root, dirs, files in os.walk(local_path):
        # Skip .git directory
        dirs[:] = [d for d in dirs if d != '.git']
        file_count += len(files)

    return {
        "status": "success",
        "local_path": local_path,
        "project_id": project_id,
        "file_count": file_count,
        "message": f"Repository cloned to {local_path} ({file_count} files)"
    }
