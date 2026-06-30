# Understand Anything — AWS Architecture

## Overview

A cloud-hosted AI Agent service that analyzes GitHub repositories, builds knowledge graphs, stores them in a graph database, and answers questions about code architecture via natural language.

## Infrastructure Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Frontend (CloudFront + S3)                                │
│  ┌──────────────────┐  ┌───────────────────────┐  ┌─────────────────────┐  │
│  │ React Dashboard   │  │ D3.js Knowledge Graph │  │ Chat Interface      │  │
│  │ (Project List,    │  │ Visualization         │  │ (Natural Language   │  │
│  │  Status, Actions) │  │ (Nodes, Edges, Layers)│  │  Q&A + Streaming)  │  │
│  └──────────────────┘  └───────────────────────┘  └─────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ JWT (Cognito ID token)
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Amazon Cognito (User Pool + App Client)                   │
│  Email/Password Sign-in │ Self-signup disabled │ JWT Issuance (RS256)        │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ Authorization: Bearer <id_token> (CUSTOM_JWT)
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Bedrock AgentCore Runtime (Streamable HTTP)               │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ main.py — Intent Router (Haiku classification → Sonnet execution)     │  │
│  │                                                                       │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐             │  │
│  │  │ analyze  │  │ neptune  │  │  query   │  │  delete  │             │  │
│  │  │ Clone +  │  │ KG JSON →│  │ NL Q&A   │  │ Drop V/E │             │  │
│  │  │ Parse +  │  │ Graph DB │  │ via Graph │  │ + S3 del │             │  │
│  │  │ Sub-agent│  │ (write)  │  │ Traversal│  │          │             │  │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘             │  │
│  │       │              │             │             │                    │  │
│  └───────┼──────────────┼─────────────┼─────────────┼────────────────────┘  │
│          │              │             │             │                        │
│  ┌───────▼──────────────▼─────────────▼─────────────▼────────────────────┐  │
│  │ Tools Layer                                                            │  │
│  │  neptune_http.py       — SigV4-signed HTTP client for Neptune          │  │
│  │  neptune_writer.py     — Write / Delete graph (batch addV + addE)      │  │
│  │  neptune_mcp_server.py — MCP tool: query_neptune (predefined Gremlin)  │  │
│  │  s3_manager.py         — Upload / Download / Delete / List projects    │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Claude Agent SDK (via Amazon Bedrock)                                   │  │
│  │  • Haiku — fast intent classification (analyze/neptune/query/delete)    │  │
│  │  • Sonnet — complex reasoning (analysis, Q&A, sub-agent orchestration) │  │
│  │  • 9 Sub-agents (.md prompts): architecture, file, dependency,         │  │
│  │    security, documentation, test, tour-builder, graph-reviewer,        │  │
│  │    project-scanner                                                     │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Tree-sitter Skills (Node.js 22)                                        │  │
│  │  parse-imports.js │ parse-structure.js │ parse-functions.js │ complexity│  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  /mnt/workspace — Session Storage (git clone target, persists in session)   │
└──────────┬──────────────────────┬───────────────────────────────────────────┘
           │ HTTP 8182            │ IAM Role (GetObject, PutObject,
           │ (VPC internal)       │  DeleteObject, ListBucket)
           ▼                      ▼
┌────────────────────────┐  ┌─────────────────────────────────────────────────┐
│ Neptune Serverless      │  │ S3 Knowledge Graphs Bucket                      │
│ (Gremlin API, 1-4 NCU) │  │                                                 │
│                         │  │  {project_id}/knowledge-graph.json              │
│ • Private subnet only   │  │  projects.json (index)                          │
│ • SG: TCP 8182 from VPC │  │                                                 │
│ • Multi-tenant by       │  │  CloudFront /graphs/* origin (OAI read-only)    │
│   project_id property   │  │                                                 │
└─────────────────────────┘  └─────────────────────────────────────────────────┘
```

### Auth Chain

```
Browser → Cognito (email/password) → ID token (JWT)
  → CloudFront → AgentCore Runtime (CUSTOM_JWT validation, allowedClients)
     ├─ Bedrock API  → IAM Role (InvokeModel, InvokeModelWithResponseStream)
     ├─ Neptune      → VPC + Security Group (port 8182, CIDR only)
     └─ S3           → IAM Role (scoped to single bucket)
```

## VPC Network Topology

```
┌─────────────────────────────────────────────────────────────────────┐
│ VPC (2 Availability Zones: us-east-1b, us-east-1c)                  │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ Public Subnets                                                 │  │
│  │                                                                │  │
│  │  ┌─────────────────┐                                          │  │
│  │  │  NAT Gateway     │──── Outbound Internet                   │  │
│  │  └─────────┬────────┘     (Bedrock API, git clone, ECR pull)  │  │
│  └────────────┼──────────────────────────────────────────────────┘  │
│               │                                                      │
│  ┌────────────▼──────────────────────────────────────────────────┐  │
│  │ Private Subnets (NAT Egress)                                   │  │
│  │                                                                │  │
│  │  ┌──────────────────────────┐    ┌─────────────────────────┐  │  │
│  │  │  AgentCore Runtime        │    │  Neptune Serverless      │  │  │
│  │  │  (Container)              │───▶│  (Graph DB, 1-4 NCU)    │  │  │
│  │  │                           │8182│                          │  │  │
│  │  │  SG: all outbound         │    │  SG: TCP 8182 from VPC  │  │  │
│  │  └──────────────────────────┘    └─────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

## AgentCore Container Internals

```
┌─────────────────────────────────────────────────────────────────────────┐
│ AgentCore Runtime Container (Python 3.11 + Node.js 22)                   │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ main.py — Entrypoint (BedrockAgentCoreApp)                         │  │
│  │                                                                    │  │
│  │  Intent Router:                                                    │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐             │  │
│  │  │ analyze  │ │ neptune  │ │  query   │ │  delete  │             │  │
│  │  │(GitHub→KG)│ │(KG→Graph)│ │(Q&A)    │ │(清除数据) │             │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘             │  │
│  └───────┼─────────────┼────────────┼────────────┼────────────────────┘  │
│          │             │            │            │                        │
│  ┌───────▼─────────────▼────────────▼────────────▼────────────────────┐  │
│  │ Tools Layer                                                         │  │
│  │                                                                     │  │
│  │  s3_manager.py         — Upload / Download / Delete / List projects │  │
│  │  neptune_writer.py     — Write / Delete graph (nodes + edges)       │  │
│  │  neptune_http.py       — SigV4-signed HTTP client for Neptune       │  │
│  │  neptune_mcp_server.py — MCP tool server (query_neptune for Claude) │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Claude Agent SDK (via Amazon Bedrock)                                │  │
│  │                                                                      │  │
│  │  • Intent Detection — Claude Haiku (fast classification)             │  │
│  │  • Analysis/Query   — Claude Sonnet (complex reasoning)              │  │
│  │  • 9 Sub-agents     — architecture, file, dependency, security,     │  │
│  │                        documentation, test, tour-builder,            │  │
│  │                        graph-reviewer, project-scanner               │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Tree-sitter Skills (Node.js)                                         │  │
│  │                                                                      │  │
│  │  • parse-imports.js       — Extract import/require statements        │  │
│  │  • parse-structure.js     — File structure & exports                  │  │
│  │  • parse-functions.js     — Function/method signatures                │  │
│  │  • analyze-complexity.js  — Cyclomatic complexity estimation          │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  /mnt/workspace — Session Storage (persists across requests in session)  │
└──────────────────────────────────────────────────────────────────────────┘
```

## Neptune Graph Schema

```
┌─────────────────────────────────────────────────────────────────────┐
│ Neptune Serverless (Gremlin API, HTTP + SigV4)                       │
│                                                                      │
│  Vertex Labels:                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────┐  ┌──────────────┐  │
│  │  project     │  │ code_entity  │  │  layer  │  │  tour_step   │  │
│  │             │  │              │  │         │  │              │  │
│  │ • name      │  │ • name       │  │ • name  │  │ • order      │  │
│  │ • languages │  │ • type       │  │ • desc  │  │ • title      │  │
│  │ • frameworks│  │ • file_path  │  │ • nodeIds│  │ • description│  │
│  │ • analyzedAt│  │ • summary    │  └─────────┘  │ • nodeIds    │  │
│  │ • version   │  │ • tags       │               └──────────────┘  │
│  │ • kind      │  │ • complexity │                                  │
│  └─────────────┘  │ • start_line │                                  │
│                    │ • end_line   │                                  │
│                    └──────┬───────┘                                  │
│                           │                                          │
│  Edge Labels:             │                                          │
│  ┌────────────────────────▼──────────────────────────────────────┐  │
│  │  imports │ calls │ contains │ depends_on │ relates_to          │  │
│  │                                                                │  │
│  │  Properties: project_id, weight, direction, description        │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  Multi-tenancy: all vertices/edges carry `project_id` property       │
└──────────────────────────────────────────────────────────────────────┘
```

## Data Flows

### Flow 1: Analyze Repository

```
Browser                AgentCore              Bedrock API         S3
  │                       │                       │               │
  │── "Analyze <URL>" ───▶│                       │               │
  │                       │── Intent(Haiku) ─────▶│               │
  │                       │◀── "analyze" ─────────│               │
  │                       │                       │               │
  │                       │── git clone ──────────────▶ GitHub    │
  │                       │                       │               │
  │                       │── Tree-sitter parse ──│               │
  │                       │                       │               │
  │                       │── Sub-agents(Sonnet)─▶│               │
  │◀── SSE streaming ────│◀── KG JSON ──────────│               │
  │                       │                       │               │
  │                       │── Upload KG ─────────────────────────▶│
  │                       │── Update projects.json ──────────────▶│
  │                       │                       │               │
```

### Flow 2: Write to Neptune

```
Browser                AgentCore              Neptune
  │                       │                      │
  │── "Write Neptune" ───▶│                      │
  │                       │── Intent: "neptune"  │
  │                       │                      │
  │                       │── check_exists() ───▶│
  │                       │◀── count=0 ──────────│
  │                       │                      │
  │                       │── DROP old data ────▶│
  │                       │── addV(project) ────▶│
  │                       │── addV(entity) ×N ──▶│
  │                       │── addE() ×M ────────▶│
  │                       │── addV(layer) ──────▶│
  │                       │── addV(tour_step) ──▶│
  │                       │                      │
  │                       │── count_vertices() ─▶│
  │◀── "Success: N nodes"─│◀── verified ─────────│
  │                       │                      │
```

### Flow 3: Query (Q&A)

```
Browser                AgentCore              Claude Sonnet       Neptune
  │                       │                       │                  │
  │── "How does X work?"─▶│                       │                  │
  │                       │── Intent: "query"     │                  │
  │                       │                       │                  │
  │                       │── Prompt + System ───▶│                  │
  │                       │                       │── query_neptune ▶│
  │                       │                       │  (MCP tool call) │
  │                       │                       │◀── graph context─│
  │                       │                       │                  │
  │◀── SSE streaming ────│◀── Answer ────────────│                  │
  │   (references files,  │                       │                  │
  │    functions, layers)  │                       │                  │
  │                       │                       │                  │
```

### Flow 4: Delete Project

```
Browser                AgentCore              Neptune            S3
  │                       │                      │               │
  │── "Delete <project>"─▶│                      │               │
  │                       │── Intent: "delete"   │               │
  │                       │                      │               │
  │                       │── DROP vertices ────▶│               │
  │                       │── DROP project node ▶│               │
  │                       │◀── verified (0) ─────│               │
  │                       │                      │               │
  │                       │── Delete KG file ───────────────────▶│
  │                       │── Update projects.json ─────────────▶│
  │◀── "Deleted OK" ─────│                      │               │
  │                       │                      │               │
```

### Flow 5: Dashboard Rendering

```
Browser                CloudFront             S3
  │                       │                    │
  │── GET / ─────────────▶│── /* ─────────────▶│ Dashboard Bucket
  │◀── React SPA ─────────│◀── index.html ────│
  │                       │                    │
  │── GET /graphs/projects.json ──▶│           │
  │                       │── /graphs/* ──────▶│ Knowledge Graphs Bucket
  │◀── Project list ──────│◀── projects.json ──│
  │                       │                    │
  │── GET /graphs/{id}/knowledge-graph.json ──▶│
  │◀── Full KG JSON ──────│◀── KG file ───────│
  │                       │                    │
  │── (Local D3.js rendering, no Neptune needed)
  │                       │                    │
```

## CI/CD Pipeline

```
Developer              CDK Deploy             CodeBuild             ECR
  │                       │                       │                   │
  │── cdk deploy ────────▶│                       │                   │
  │                       │── Upload S3 Asset ───▶│ (source stored)   │
  │                       │── Update CFN Stack    │                   │
  │                       │                       │                   │
  │── Start Build ────────────────────────────────▶│                   │
  │                       │                       │── docker build    │
  │                       │                       │── docker push ───▶│
  │                       │                       │   (:latest + tag) │
  │                       │                       │                   │
  │── force_update_runtime.py ─────────────────────────────────────────▶
  │   (update_agent_runtime API with image digest)                     │
  │                                                                    │
  │◀── Runtime pulls new image, creates new version ◀──────────────────│
  │                       │                       │                   │
```

## Security Model

| Layer | Mechanism |
|-------|-----------|
| User → CloudFront | HTTPS only (redirect HTTP) |
| User → AgentCore | Cognito JWT (allowedClients validation) |
| AgentCore → Neptune | VPC isolation + Security Group (port 8182, VPC CIDR only) |
| AgentCore → S3 | IAM Role (GetObject, PutObject, DeleteObject, ListBucket) |
| AgentCore → Bedrock | IAM Role (InvokeModel, InvokeModelWithResponseStream) |
| Neptune | Private subnet, no public endpoint, IAM auth disabled (POC) |
| S3 Buckets | BlockPublicAccess, OAI for CloudFront read-only |
| ECR | IAM-gated pull (Runtime Role), push (CodeBuild Role) |

## CDK Stack Dependencies

```
         ┌─────────┐
         │ VPC     │
         └────┬────┘
              │
     ┌────────┼────────┐
     ▼        ▼        │
┌─────────┐ ┌────────┐ │
│ Neptune │ │Cognito │ │
└────┬────┘ └───┬────┘ │
     │          │      │
     │    ┌─────┘      │     ┌─────┐
     │    │            │     │ S3  │
     │    │            │     └──┬──┘
     ▼    ▼            ▼        │
   ┌──────────────────────┐    │
   │     AgentCore         │◄───┘
   └──────────┬────────────┘
              │
              ▼
   ┌──────────────────────┐
   │     Frontend          │
   └───────────────────────┘
```
