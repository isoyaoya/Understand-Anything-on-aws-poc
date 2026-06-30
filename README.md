# Understand Anything — Cloud Edition

Deploy [Understand-Anything](https://github.com/Egonex-AI/Understand-Anything) (a code knowledge graph tool) as a cloud-hosted AI Agent service on AWS.

Analyze any public GitHub repository, build a knowledge graph, store it in a graph database, and ask questions about the codebase in natural language.

## Architecture

```
CloudFront → S3 (React Dashboard)
CloudFront /graphs/* → S3 Knowledge Graphs (JSON)
Browser → Cognito JWT → AgentCore Runtime (streamable HTTP)
                         ├─ Main Agent (Claude Agent SDK)
                         ├─ Sub-agents (9 agent .md definitions)
                         ├─ Custom Tools (clone, neptune_writer, query_neptune, s3_manager)
                         ├─ Tree-sitter (Node.js, 4 parsing scripts)
                         ├─ S3 persistence (post-analysis upload)
                         └─ Neptune graph DB (for Q&A queries)
Dashboard → CloudFront → S3 (knowledge-graph.json per project)
Chat Q&A → AgentCore → Neptune (graph traversal + context building)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed diagrams, data flows, and security model.

## Demo

### 1. Analyze a Repository

Paste a GitHub URL and the agent clones the repo, runs Tree-sitter parsing + LLM semantic extraction, and produces a full knowledge graph.

![Analyze - Processing](images/analyze-repo-1.png)

The agent executes a multi-phase workflow: project scan, file analysis (batched + concurrent), architecture layer detection, tour building, and graph assembly — then uploads the result to S3.

![Analyze - Complete](images/analyze-repo-2.png)

### 2. Write to Neptune

Persist the knowledge graph to Neptune Serverless. The agent writes all nodes and edges, then verifies the count.

![Write to Neptune](images/write-to-neptune.png)

### 3. Query (Natural Language Q&A)

Ask questions about the codebase in natural language. The agent queries Neptune graph traversals and builds a comprehensive answer with file paths, function names, and architectural context.

![Query - Graph Traversal](images/query-chat-1.png)

![Query - Detailed Answer](images/query-chat-2.png)

## Features

- **Analyze** — provide a GitHub URL, the agent clones and runs full analysis (Tree-sitter + LLM semantic extraction)
- **Write to Neptune** — persist the knowledge graph to Neptune Serverless for fast graph queries
- **Query** — ask natural language questions; the agent queries Neptune and answers with file/function/layer context
- **Delete** — remove a project from both Neptune and S3

## Project Structure

```
understand-anything-cloud-poc/
├── agentcore/            # Agent container
│   ├── main.py           # Entrypoint (intent router + BedrockAgentCoreApp)
│   ├── Dockerfile        # Python 3.11 + Node.js 22 + git
│   ├── tools/            # neptune_http, neptune_writer, neptune_mcp_server, s3_manager
│   ├── agents/           # 9 sub-agent prompt definitions (.md)
│   ├── skills/           # Tree-sitter parsing scripts
│   └── packages/core/    # @understand-anything/core (Tree-sitter wrappers)
├── dashboard/            # React SPA (knowledge graph visualization)
├── infra/                # CDK Python (6 stacks)
│   ├── app.py
│   └── stacks/
│       ├── vpc_stack.py
│       ├── neptune_stack.py
│       ├── cognito_stack.py
│       ├── agentcore_stack.py
│       ├── s3_stack.py
│       └── frontend_stack.py
└── docs/                 # Architecture documentation
```

## CDK Stacks

| Stack | Purpose |
|-------|---------|
| VPC | 2 AZ, NAT Gateway (outbound internet for Bedrock API, git clone) |
| Neptune | Neptune Serverless graph database (1–4 NCU, private subnet) |
| Cognito | User Pool + App Client (JWT auth for AgentCore) |
| AgentCore | ECR + CodeBuild (ARM64) + Bedrock AgentCore Runtime |
| S3 | Knowledge graph JSON storage + CloudFront OAI |
| Frontend | S3 static hosting + CloudFront (dashboard + /graphs/* origin) |

## Prerequisites

- AWS CLI configured with appropriate credentials
- Node.js 18+ and npm
- Python 3.11+ and pip
- AWS CDK v2 (`npm install -g aws-cdk`)

## Deployment

### 1. Deploy Infrastructure

```bash
cd infra
pip install -r requirements.txt
cdk bootstrap
cdk deploy --all
```

CDK reads your AWS account/region from environment variables (`CDK_DEFAULT_ACCOUNT`, `CDK_DEFAULT_REGION`) or your AWS CLI profile.

### 2. Build & Push Agent Container

```bash
# Trigger CodeBuild (builds Docker image and pushes to ECR)
aws codebuild start-build --project-name ua-v2-build --region us-east-1
```

### 3. Update AgentCore Runtime

After CodeBuild pushes the new image, force the Runtime to pull it:

```bash
# Get the latest image digest
DIGEST=$(aws ecr describe-images \
  --repository-name ua-v2-agent \
  --image-ids imageTag=latest \
  --query 'imageDetails[0].imageDigest' --output text)

# Call update_agent_runtime with the digest URI to trigger a new version
# (The Runtime won't pull a new image unless containerUri actually changes)
```

See the `update_agent_runtime` [API docs](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/update_agent_runtime.html) for the full call.

### 4. Local Testing

```bash
cd agentcore
pip install -r requirements.txt
export CLAUDE_CODE_USE_BEDROCK=1
export AWS_DEFAULT_REGION=us-east-1
python main.py
# POST http://localhost:8080/invocations
```

## Key Design Decisions

1. **Bedrock AgentCore Runtime** — no Lambda timeout limits, native streamable HTTP, session storage
2. **Clone to /mnt/workspace** — sub-agents need direct filesystem access for Tree-sitter parsing
3. **Tree-sitter retained** — deterministic parsing is a core advantage over pure-LLM extraction
4. **Fixed Gremlin templates** — no LLM-generated queries; MCP tool with predefined query patterns
5. **Intent-based routing** — Haiku classifies intent cheaply, Sonnet handles complex work
6. **Image digest for deploys** — static tags (`:latest`) don't trigger Runtime updates; use `@sha256:...`

## Cost Estimate (Monthly, POC usage)

| Resource | Estimated Cost |
|----------|---------------|
| NAT Gateway | ~$32 |
| Neptune Serverless (1-4 NCU) | ~$10–30 |
| AgentCore Runtime | ~$30–50 |
| Claude API via Bedrock | ~$50–100 |
| S3 + CloudFront | ~$5 |
| **Total** | **~$100–190/month** |

## License

MIT
