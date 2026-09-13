#!/usr/bin/env python3
"""Create or update the tactical-tools MCP Gateway and its Lambda targets.

Called by deploy-all.sh after the tool Lambdas are deployed. Tool schemas come
from gateway_tools/tool_schemas.json — the same file gateway_tools/test_gateway.py
checks the Lambdas against — and existing targets are updated, so schema
changes land on redeploy.

Output (stdout): GATEWAY_ID=<id> and GATEWAY_URL=<url>. Progress goes to stderr.

Env vars:
  AWS_DEFAULT_REGION  (default: us-east-1)
  AWS_ACCOUNT_ID      required
  GATEWAY_ROLE_ARN    required — role the gateway assumes to invoke the Lambdas
  GATEWAY_NAME        (default: alchemy-tactical-tools)
  LAMBDA_PREFIX       (default: alchemy-gateway-tool)
"""

import json
import os
import sys
import time
from pathlib import Path

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
GATEWAY_NAME = os.environ.get("GATEWAY_NAME", "alchemy-tactical-tools")
LAMBDA_PREFIX = os.environ.get("LAMBDA_PREFIX", "alchemy-gateway-tool")
SCHEMAS_FILE = Path(__file__).resolve().parent / "gateway_tools" / "tool_schemas.json"

POLL_SECONDS = 5
MAX_WAIT_SECONDS = 300
FAILED_STATUSES = ("FAILED", "UPDATE_UNSUCCESSFUL", "SYNCHRONIZE_UNSUCCESSFUL")


def log(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def fail(msg: str) -> None:
    log(f"ERROR: {msg}")
    sys.exit(1)


def paginate(call, key: str, **kwargs):
    token = None
    while True:
        resp = call(**kwargs, **({"nextToken": token} if token else {}))
        yield from resp.get(key, [])
        token = resp.get("nextToken")
        if not token:
            return


def wait_ready(describe, label: str) -> dict:
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        resp = describe()
        status = resp.get("status")
        if status == "READY":
            return resp
        if status in FAILED_STATUSES:
            fail(f"{label} is {status}: {resp.get('statusReasons')}")
        if time.monotonic() > deadline:
            fail(f"{label} not READY after {MAX_WAIT_SECONDS}s (status {status})")
        log(f"{label}: {status} (waiting...)")
        time.sleep(POLL_SECONDS)


def main() -> None:
    for var in ("AWS_ACCOUNT_ID", "GATEWAY_ROLE_ARN"):
        if not os.environ.get(var):
            fail(f"{var} environment variable is required")
    if "bedrock-agentcore-control" not in boto3.session.Session().get_available_services():
        fail(
            f"boto3 {boto3.__version__} predates AgentCore Gateway. Upgrade it "
            "(pip install -U boto3) or set GATEWAY_PYTHON to a Python with a newer boto3."
        )
    account_id = os.environ["AWS_ACCOUNT_ID"]
    role_arn = os.environ["GATEWAY_ROLE_ARN"]
    client = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # ---- Gateway ----
    existing_gw = next(
        (g for g in paginate(client.list_gateways, "items") if g.get("name") == GATEWAY_NAME),
        None,
    )
    if existing_gw:
        gateway_id = existing_gw["gatewayId"]
        log(f"Gateway exists: {GATEWAY_NAME} ({gateway_id})")
    else:
        log(f"Creating MCP Gateway: {GATEWAY_NAME} (NONE auth)")
        gateway_id = client.create_gateway(
            name=GATEWAY_NAME,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="NONE",
        )["gatewayId"]
    gateway = wait_ready(lambda: client.get_gateway(gatewayIdentifier=gateway_id), f"gateway {GATEWAY_NAME}")

    # ---- Targets (one Lambda per tool) ----
    existing_targets = {
        t["name"]: t["targetId"]
        for t in paginate(client.list_gateway_targets, "items", gatewayIdentifier=gateway_id)
    }
    for entry in json.loads(SCHEMAS_FILE.read_text(encoding="utf-8")):
        target = entry["target"]
        params = {
            "gatewayIdentifier": gateway_id,
            "name": target,
            "description": f"Tactical tool {entry['tool']['name']}",
            "targetConfiguration": {
                "mcp": {
                    "lambda": {
                        "lambdaArn": f"arn:aws:lambda:{REGION}:{account_id}:function:{LAMBDA_PREFIX}-{target}",
                        "toolSchema": {"inlinePayload": [entry["tool"]]},
                    }
                }
            },
            "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        }
        if target in existing_targets:
            target_id = existing_targets[target]
            log(f"Updating target: {target}")
            client.update_gateway_target(targetId=target_id, **params)
        else:
            log(f"Creating target: {target}")
            target_id = client.create_gateway_target(**params)["targetId"]
        wait_ready(
            lambda: client.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id),
            f"target {target}",
        )

    print(f"GATEWAY_ID={gateway_id}")
    print(f"GATEWAY_URL={gateway['gatewayUrl']}")


if __name__ == "__main__":
    main()
