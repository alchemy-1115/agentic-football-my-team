"""One-time script to create the AgentCore Memory resource for my-team.

Creates a short-term memory (STM) resource for cross-tick recall. No long-term
strategies are defined — raw events are retained for the match session only.

Usage:
    AWS_DEFAULT_REGION=us-east-1 python3 create_memory.py

Prints the MEMORY_ID. deploy-all.sh runs this automatically when MEMORY_ID is
unset, so you only need it directly to look the ID up:
    export MEMORY_ID=<printed-id>
"""

import os
from bedrock_agentcore.memory import MemoryClient

MEMORY_NAME = "MyTeamMatchMemory"

region = os.environ.get("AWS_DEFAULT_REGION")
if not region:
    raise RuntimeError("AWS_DEFAULT_REGION environment variable is required")

client = MemoryClient(region_name=region)


def find_existing():
    for mem in client.list_memories():
        mem_id = mem.get("id") or mem.get("memoryId", "")
        if mem.get("name") == MEMORY_NAME or mem_id.startswith(MEMORY_NAME):
            return mem_id
    return None


memory_id = find_existing()

if not memory_id:
    try:
        memory = client.create_memory(
            name=MEMORY_NAME,
            description="Short-term memory for my-team soccer agents — game tick history within a match",
        )
        memory_id = memory.get("id") or memory.get("memoryId")
    except Exception as e:
        if "already exists" not in str(e):
            raise
        memory_id = find_existing()
        if not memory_id:
            raise RuntimeError(f"Memory '{MEMORY_NAME}' exists but its ID could not be retrieved: {e}")

print(f"Memory resource ready: {memory_id}")
print(f"Export it:  export MEMORY_ID={memory_id}")
