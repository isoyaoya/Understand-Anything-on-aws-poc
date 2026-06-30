"""
Neptune Gremlin Client - shared connection logic
"""

import os
from gremlin_python.driver import client as gremlin_client

NEPTUNE_ENDPOINT = os.environ.get("NEPTUNE_ENDPOINT", "")
NEPTUNE_PORT = os.environ.get("NEPTUNE_PORT", "8182")


def create_neptune_client():
    """Create a new Neptune Gremlin client connection"""
    endpoint = f"wss://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/gremlin"
    return gremlin_client.Client(endpoint, 'g')
