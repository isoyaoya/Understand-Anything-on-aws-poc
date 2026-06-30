"""Neptune HTTP client — SigV4-signed POST to /gremlin.

Replaces gremlin_python for write operations. Uses requests + botocore
for a simple, synchronous HTTP interface with no Tornado dependency.
"""

import json
import logging
import os

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = logging.getLogger("neptune_http")


class NeptuneHttpClient:
    """Stateless Neptune Gremlin client over HTTP POST with SigV4 auth."""

    def __init__(self, endpoint=None, port=None, region=None):
        self.endpoint = endpoint or os.environ.get("NEPTUNE_ENDPOINT", "")
        self.port = port or os.environ.get("NEPTUNE_PORT", "8182")
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._request_count = 0
        print(f"[neptune_http] init: endpoint={self.endpoint}, port={self.port}, region={self.region}", flush=True)
        self._session = boto3.Session(region_name=self.region)
        creds = self._session.get_credentials()
        if creds is None:
            raise RuntimeError("No AWS credentials available for Neptune SigV4")
        self._signer = SigV4Auth(
            creds.get_frozen_credentials(), "neptune-db", self.region
        )
        print(f"[neptune_http] SigV4 auth initialized OK", flush=True)

    @property
    def url(self) -> str:
        return f"https://{self.endpoint}:{self.port}/gremlin"

    def execute(self, gremlin: str, timeout: int = 30) -> dict:
        """Execute a single Gremlin statement via HTTP POST.

        Returns the Neptune JSON response dict.
        Raises requests.HTTPError on 4xx/5xx.
        """
        if not self.endpoint:
            raise RuntimeError(f"NEPTUNE_ENDPOINT not configured (endpoint={self.endpoint!r})")

        body = json.dumps({"gremlin": gremlin})
        aws_req = AWSRequest(
            method="POST",
            url=self.url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        self._signer.add_auth(aws_req)

        resp = requests.post(
            self.url,
            data=body,
            headers=dict(aws_req.headers),
            timeout=timeout,
        )

        if resp.status_code >= 400:
            logger.error(
                "Neptune HTTP %s: query=%s response=%s",
                resp.status_code,
                gremlin[:200],
                resp.text[:300],
            )
        resp.raise_for_status()
        return resp.json()

    def count_vertices(self, project_id: str, label: str = "code_entity") -> int:
        """Count vertices for a project+label. Used for write verification."""
        pid = escape_gremlin(project_id)
        result = self.execute(
            f"g.V().has('project_id', '{pid}').hasLabel('{label}').count()"
        )
        return self._parse_count(result)

    def _parse_count(self, result: dict) -> int:
        """Parse Neptune count response, handling both list and GraphSON formats."""
        data = result.get("result", {}).get("data", {})
        # GraphSON format: {"@type": "g:List", "@value": [{"@type": "g:Int64", "@value": 18}]}
        if isinstance(data, dict) and "@value" in data:
            values = data["@value"]
            if values and isinstance(values[0], dict) and "@value" in values[0]:
                return int(values[0]["@value"])
            if values and isinstance(values[0], (int, float)):
                return int(values[0])
            return 0
        # Plain list format: [18]
        if isinstance(data, list):
            if data and isinstance(data[0], (int, float)):
                return int(data[0])
            if data and isinstance(data[0], dict) and "@value" in data[0]:
                return int(data[0]["@value"])
            return 0
        # Single value
        if isinstance(data, (int, float)):
            return int(data)
        print(f"[neptune_http] _parse_count unexpected format: {data!r}", flush=True)
        return 0


def escape_gremlin(s) -> str:
    """Escape string for safe inclusion in a Gremlin query (single quotes)."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace('"', '\\"')
        .replace("`", "")
        .replace("\x00", "")
    )
