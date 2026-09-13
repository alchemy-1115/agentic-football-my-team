#!/bin/bash
set -e

# ============================================================================
# Deploy all 5 AI Team agents to Bedrock AgentCore
# ============================================================================
#
# Usage:
#   AWS_PROFILE=your-profile ./deploy-all.sh          # deploy all
#   AWS_PROFILE=your-profile ./deploy-all.sh ai-gk    # deploy one agent
#
# AgentCore Memory is created automatically when MEMORY_ID is unset.
# Reuse an existing one with:  MEMORY_ID=xxx ./deploy-all.sh
#
# How it works:
#   1. Ensures the AgentCore Memory resource exists (create_memory.py)
#   2. Creates a _build/<agent>/ staging directory for each agent
#   3. Copies the agent's src/ + shared lib/ + requirements.txt into it
#   4. Generates .bedrock_agentcore.yaml from the agent's template
#   5. Deploys from the staging directory, injecting MEMORY_ID / AGENT_POSITION
#   6. Attaches memory permissions to the agent execution roles
#   7. Cleans up _build/ when done
#
# This avoids copying lib/ into each agent's source tree. You only ever
# edit lib/ in one place.
#
# Agents in GATEWAY_AGENTS (ai-mid) also get the tactical-tools MCP Gateway:
# the Lambdas in gateway_tools/, their IAM roles, and the Gateway + targets
# (manage_gateway.py) are created or updated first, and the Gateway endpoint
# is passed to those agents as GATEWAY_URL. Pre-set GATEWAY_URL to skip that.
#
# Prerequisites:
#   pip install bedrock-agentcore-starter-toolkit
#   aws configure (or set AWS_PROFILE)
#   zip, and a python3 whose boto3 knows bedrock-agentcore-control
#   (override with GATEWAY_PYTHON=/path/to/python)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/_build"

AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_DEFAULT_REGION

ALL_AGENTS=("ai-gk" "ai-def1" "ai-def2" "ai-mid" "ai-fwd")

# If an agent name was passed as argument, deploy only that one
if [ -n "$1" ]; then
  AGENTS=("$1")
else
  AGENTS=("${ALL_AGENTS[@]}")
fi

echo "=========================================="
echo "  AI Team — Deploy Agents"
echo "=========================================="
echo ""

# ------ Pre-flight ------
echo "Checking prerequisites..."

if ! command -v agentcore &> /dev/null; then
  echo "ERROR: 'agentcore' CLI not found."
  echo "Install: pip install bedrock-agentcore-starter-toolkit"
  exit 1
fi
echo "  agentcore CLI: OK"

if ! command -v rsync &> /dev/null; then
  echo "ERROR: 'rsync' not found (needed to copy lib without __pycache__)."
  exit 1
fi
echo "  rsync: OK"

if ! command -v aws &> /dev/null; then
  echo "ERROR: 'aws' CLI not found."
  exit 1
fi
echo "  aws CLI: OK"

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) || {
  echo "ERROR: No valid AWS credentials."
  exit 1
}
export AWS_ACCOUNT_ID
echo "  AWS Account: $AWS_ACCOUNT_ID"
echo "  AWS Region:  $AWS_DEFAULT_REGION"
echo ""

# ------ AgentCore Memory resource ------
if [ -z "$MEMORY_ID" ]; then
  echo "MEMORY_ID not set — creating/looking up the memory resource..."
  CREATE_OUTPUT=$(python3 "$SCRIPT_DIR/create_memory.py" 2>&1) || {
    echo "$CREATE_OUTPUT"
    echo "ERROR: create_memory.py failed."
    exit 1
  }
  MEMORY_ID=$(echo "$CREATE_OUTPUT" | sed -n 's/.*Memory resource ready: \([^ ]*\).*/\1/p')
  if [ -z "$MEMORY_ID" ]; then
    echo "$CREATE_OUTPUT"
    echo "ERROR: Could not parse MEMORY_ID from create_memory.py output."
    exit 1
  fi
  echo "  MEMORY_ID: $MEMORY_ID (created/found)"
else
  echo "  MEMORY_ID: $MEMORY_ID (from env)"
fi
export MEMORY_ID
TEAM_ID="${TEAM_ID:-my-team}"
export TEAM_ID
echo "  TEAM_ID:   $TEAM_ID"
echo ""

# ------ Tactical-tools Gateway (only for GATEWAY_AGENTS) ------
GATEWAY_AGENTS=("ai-mid")
GATEWAY_NAME="alchemy-tactical-tools"
LAMBDA_PREFIX="afwc-gateway-tool"
LAMBDA_ROLE_NAME="afwc-gateway-tool-lambda-role"
GW_ROLE_NAME="AfwcGatewayExecutionRole"
GATEWAY_PYTHON="${GATEWAY_PYTHON:-python3}"

is_gateway_agent() {
  local a
  for a in "${GATEWAY_AGENTS[@]}"; do
    [ "$a" = "$1" ] && return 0
  done
  return 1
}

NEEDS_GATEWAY=false
for agent in "${AGENTS[@]}"; do
  if is_gateway_agent "$agent"; then NEEDS_GATEWAY=true; fi
done

if $NEEDS_GATEWAY && [ -n "$GATEWAY_URL" ]; then
  echo "Using pre-set GATEWAY_URL: $GATEWAY_URL"
  echo ""
elif $NEEDS_GATEWAY; then
  if ! command -v zip &> /dev/null; then
    echo "ERROR: 'zip' not found (needed to package the tool Lambdas)."
    exit 1
  fi

  echo "=========================================="
  echo "  Gateway 1/4: Lambda execution role"
  echo "=========================================="
  if LAMBDA_ROLE_ARN=$(aws iam get-role --role-name "$LAMBDA_ROLE_NAME" --query 'Role.Arn' --output text 2>/dev/null); then
    echo "  Reusing: $LAMBDA_ROLE_ARN"
  else
    LAMBDA_ROLE_ARN=$(aws iam create-role --role-name "$LAMBDA_ROLE_NAME" \
      --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
      --query 'Role.Arn' --output text)
    aws iam attach-role-policy --role-name "$LAMBDA_ROLE_NAME" \
      --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
    echo "  Created: $LAMBDA_ROLE_ARN (waiting 10s for IAM propagation)"
    sleep 10
  fi
  echo ""

  echo "=========================================="
  echo "  Gateway 2/4: Tool Lambdas"
  echo "=========================================="
  # "<target> <module>" per tool — the same schema file manage_gateway.py registers
  TOOL_LINES=$(python3 -c 'import json, sys; [print(t["target"], t["module"]) for t in json.load(open(sys.argv[1]))]' \
    "$SCRIPT_DIR/gateway_tools/tool_schemas.json")
  # Read from fd 3 so no command inside the loop can swallow the remaining lines
  while read -r target module <&3; do
    FUNC_NAME="${LAMBDA_PREFIX}-${target}"
    ZIP_DIR=$(mktemp -d)
    (cd "$SCRIPT_DIR/gateway_tools" && zip -q "$ZIP_DIR/function.zip" "${module}.py" _game.py)
    if aws lambda get-function --function-name "$FUNC_NAME" > /dev/null 2>&1; then
      echo "  Updating: $FUNC_NAME"
      aws lambda update-function-code --function-name "$FUNC_NAME" \
        --zip-file "fileb://$ZIP_DIR/function.zip" > /dev/null
      aws lambda wait function-updated-v2 --function-name "$FUNC_NAME"
    else
      echo "  Creating: $FUNC_NAME"
      aws lambda create-function --function-name "$FUNC_NAME" \
        --runtime python3.12 --handler "${module}.lambda_handler" \
        --role "$LAMBDA_ROLE_ARN" --zip-file "fileb://$ZIP_DIR/function.zip" \
        --timeout 10 --memory-size 256 > /dev/null
      aws lambda wait function-active-v2 --function-name "$FUNC_NAME"
    fi
    rm -rf "$ZIP_DIR"
  done 3<<< "$TOOL_LINES"
  echo ""

  echo "=========================================="
  echo "  Gateway 3/4: Gateway execution role"
  echo "=========================================="
  GW_ROLE_CREATED=false
  if GW_ROLE_ARN=$(aws iam get-role --role-name "$GW_ROLE_NAME" --query 'Role.Arn' --output text 2>/dev/null); then
    echo "  Reusing: $GW_ROLE_ARN"
  else
    GW_ROLE_ARN=$(aws iam create-role --role-name "$GW_ROLE_NAME" \
      --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
      --query 'Role.Arn' --output text)
    GW_ROLE_CREATED=true
    echo "  Created: $GW_ROLE_ARN"
  fi
  # Only on creation: the workshop-provided role already allows afwc-gateway-tool-*,
  # and the participant role has no iam:PutRolePolicy on existing roles.
  if $GW_ROLE_CREATED; then
    aws iam put-role-policy --role-name "$GW_ROLE_NAME" --policy-name InvokeTacticalToolLambdas \
      --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"lambda:InvokeFunction\",\"Resource\":\"arn:aws:lambda:${AWS_DEFAULT_REGION}:${AWS_ACCOUNT_ID}:function:${LAMBDA_PREFIX}-*\"}]}"
    echo "  Waiting 10s for IAM propagation..."
    sleep 10
  fi
  echo ""

  echo "=========================================="
  echo "  Gateway 4/4: MCP Gateway + targets"
  echo "=========================================="
  GW_OUTPUT=$(GATEWAY_ROLE_ARN="$GW_ROLE_ARN" GATEWAY_NAME="$GATEWAY_NAME" LAMBDA_PREFIX="$LAMBDA_PREFIX" \
    "$GATEWAY_PYTHON" "$SCRIPT_DIR/manage_gateway.py") || {
    echo "ERROR: manage_gateway.py failed."
    exit 1
  }
  GATEWAY_URL=$(printf '%s\n' "$GW_OUTPUT" | sed -n 's/^GATEWAY_URL=//p')
  if [ -z "$GATEWAY_URL" ]; then
    echo "ERROR: manage_gateway.py printed no GATEWAY_URL."
    exit 1
  fi
  echo "  Gateway URL: $GATEWAY_URL"
  echo ""
fi

# ------ Cleanup on exit ------
cleanup() {
  echo ""
  echo "Cleaning up build directory..."
  rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

# ------ Build & deploy each agent ------
DEPLOYED=()
FAILED=()

for agent in "${AGENTS[@]}"; do
  AGENT_SRC="$SCRIPT_DIR/$agent"
  STAGE="$BUILD_DIR/$agent"

  echo "=========================================="
  echo "  Deploying: $agent"
  echo "=========================================="

  # Validate agent directory exists
  if [ ! -d "$AGENT_SRC" ]; then
    echo "  ERROR: Agent directory not found: $AGENT_SRC"
    FAILED+=("$agent")
    continue
  fi

  # Assemble staging directory
  rm -rf "$STAGE"
  mkdir -p "$STAGE/src"

  # Copy agent source
  cp "$AGENT_SRC/src/main.py" "$STAGE/src/main.py"

  # Copy shared lib (the whole point — single source of truth)
  # Use rsync to exclude __pycache__
  rsync -a --exclude='__pycache__' "$SCRIPT_DIR/lib/" "$STAGE/lib/"

  # Copy requirements.txt
  cp "$AGENT_SRC/requirements.txt" "$STAGE/requirements.txt"

  # Generate .bedrock_agentcore.yaml from template
  sed \
    -e "s|\${AWS_ACCOUNT_ID}|$AWS_ACCOUNT_ID|g" \
    -e "s|\${AWS_DEFAULT_REGION}|$AWS_DEFAULT_REGION|g" \
    "$AGENT_SRC/.bedrock_agentcore.yaml.template" > "$STAGE/.bedrock_agentcore.yaml"

  # Deploy from staging directory
  # Gateway agents get the MCP endpoint; everyone else deploys unchanged
  DEPLOY_ARGS=(--auto-update-on-conflict
    --env "MEMORY_ID=$MEMORY_ID"
    --env "TEAM_ID=$TEAM_ID"
    --env "AGENT_POSITION=$agent"
    --env "AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION")
  if is_gateway_agent "$agent"; then
    DEPLOY_ARGS+=(--env "GATEWAY_URL=$GATEWAY_URL")
  fi

  echo "  Deploying from: $STAGE"
  if (cd "$STAGE" && agentcore deploy "${DEPLOY_ARGS[@]}"); then
    echo "  ✅ $agent: DEPLOYED"
    DEPLOYED+=("$agent")
  else
    echo "  ❌ $agent: FAILED"
    FAILED+=("$agent")
  fi
  echo ""
done

# ------ Attach memory permissions to execution roles ------
# The auto-created execution role has no AgentCore Memory permissions, so the
# agents would get AccessDenied on their first ListEvents/CreateEvent call.
echo "Attaching AgentCore Memory permissions to execution roles..."
set +e
EXEC_ROLES=$(aws iam list-roles \
  --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKRuntime-${AWS_DEFAULT_REGION}-')].RoleName" \
  --output text 2>/dev/null)
set -e

if [ -n "$EXEC_ROLES" ]; then
  for EXEC_ROLE_NAME in $EXEC_ROLES; do
    aws iam put-role-policy \
      --role-name "$EXEC_ROLE_NAME" \
      --policy-name AgentCoreMemoryAccess \
      --policy-document "{
        \"Version\": \"2012-10-17\",
        \"Statement\": [{
          \"Effect\": \"Allow\",
          \"Action\": [
            \"bedrock-agentcore:ListEvents\",
            \"bedrock-agentcore:CreateEvent\",
            \"bedrock-agentcore:GetEvent\",
            \"bedrock-agentcore:DeleteEvent\",
            \"bedrock-agentcore:RetrieveMemoryRecords\",
            \"bedrock-agentcore:GetMemoryRecord\",
            \"bedrock-agentcore:ListMemoryRecords\"
          ],
          \"Resource\": \"arn:aws:bedrock-agentcore:${AWS_DEFAULT_REGION}:${AWS_ACCOUNT_ID}:memory/*\"
        }]
      }" 2>/dev/null && echo "  ✅ Memory permissions attached to: $EXEC_ROLE_NAME" \
      || echo "  ⚠️  Failed to attach memory permissions to: $EXEC_ROLE_NAME"
  done
else
  echo "  ⚠️  Could not find execution roles — attach AgentCoreMemoryAccess policy manually"
fi
echo ""

# ------ Summary ------
echo "=========================================="
echo "  Deployment Summary"
echo "=========================================="
echo ""
echo "  Deployed: ${DEPLOYED[*]:-none}"
echo "  Failed:   ${FAILED[*]:-none}"
echo "  Account:  $AWS_ACCOUNT_ID"
echo "  Region:   $AWS_DEFAULT_REGION"
echo "  Memory:   $MEMORY_ID"
if $NEEDS_GATEWAY; then
  echo "  Gateway:  $GATEWAY_URL"
fi
echo ""

if [ ${#FAILED[@]} -gt 0 ]; then
  echo "Some agents failed to deploy. Check the output above."
  exit 1
fi

echo "All agents deployed successfully."
