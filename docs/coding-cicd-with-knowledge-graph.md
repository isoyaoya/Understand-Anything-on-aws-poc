# Coding CI/CD + Code Knowledge Graph — Integrated Architecture

## Background

The customer's Coding CI/CD pipeline (built on Claude Agent SDK + Bedrock AgentCore Runtime) automates the full software delivery cycle from PRD to production. To improve code quality and agent comprehension on large codebases, a **Code Knowledge Graph** (powered by Neptune) is integrated so that every agent can query architectural context in real time.

## Phase 1: Preparation — Build the Knowledge Graph

```
┌─────────────────────────────────────────────────────────────────────────┐
│              Understand Anything on AWS (Analysis Pipeline)               │
│                                                                          │
│   Code Repository ──▶ Clone ──▶ Tree-sitter Parse                       │
│                            ──▶ LLM Semantic Extraction                   │
│                            ──▶ Knowledge Graph JSON                      │
│                            ──▶ Write to Neptune                          │
│                                                                          │
│   Output: Nodes (files, functions, classes, modules, layers)            │
│           Edges (imports, calls, depends_on, contains)                   │
│           Properties (complexity, tags, summary, file_path)             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Phase 2: Usage — CI/CD Agents Query the Knowledge Graph

```
┌─ Bedrock AgentCore Runtime ─────────────────────┐   ┌─ Neptune MCP Server ─────────────────────┐
│  (Claude Agent SDK)                              │   │ (Inside AgentCore Runtime)               │
│                                                  │   │                                          │
│  Coding CI/CD Pipeline                           │   │  Tool: query_neptune                     │
│                                                  │   │  Input: project_id + query keyword       │
│  All agents query via MCP ───────────────────────┼──▶│  Function: Knowledge Graph Search        │
│                                                  │   │    • Multi-field search (name, summary,  │
│  ┌────────────────┐                              │   │      tags) + 1-hop graph expansion       │
│  │ PRD Review     │                              │   │    • Returns formatted context with      │
│  │ Agent          │                              │   │      nodes, edges, layers                │
│  └───────┬────────┘                              │   │                                          │
│          │                                       │   │  Query Types:                             │
│          ▼                                       │   │    • build_chat_context                   │
│  ┌────────────────┐                              │   │    • get_nodes_by_type                    │
│  │ Design Agent   │                              │   │    • get_node_neighbors                   │
│  └───────┬────────┘                              │   │    • get_layers                           │
│          │                                       │   │    • get_full_graph                       │
│          ▼                                       │   │                                          │
│  ┌────────────────┐                              │   └──────────────────────────────────────────┘
│  │ Coding Agent   │                              │                       ▲
│  │ (Impl + Unit   │                              │                       │ Gremlin API
│  │  Test)         │                              │                       ▼
│  └───────┬────────┘                              │   ┌─ Amazon Neptune Serverless ──────────────┐
│          │                                       │   │                                          │
│          ▼                                       │   │  Code Knowledge Graph                    │
│  ┌──────────────────────────────────────┐        │   │                                          │
│  │  Bug Fix Loop                        │        │   │  Nodes:                                  │
│  │                                      │        │   │   • files, functions, classes            │
│  │  ┌──────────┐ Deploy ┌────────────┐  │        │   │   • modules, layers                      │
│  │  │ CI/CD    │───────▶│ QA Agent   │  │        │   │                                          │
│  │  │ Agent    │        │(Integration│  │        │   │  Edges:                                  │
│  │  └──────────┘        │ Test)      │  │        │   │   • imports, calls                       │
│  │       ▲              └─────┬──────┘  │        │   │   • depends_on, contains                 │
│  │       │                    │         │        │   │                                          │
│  │       │ Fix                │Bug Found│        │   │  Properties:                             │
│  │       │                    │         │        │   │   • complexity, tags, summary            │
│  │  ┌────┴───────┐           │         │        │   │   • file_path, start_line, end_line      │
│  │  │ Bug Fix    │◀──────────┘         │        │   │                                          │
│  │  │ Agent      │                     │        │   │  Multi-tenancy:                           │
│  │  └────────────┘                     │        │   │   all vertices/edges carry project_id    │
│  │                                      │        │   │                                          │
│  └──────────────────────────────────────┘        │   └──────────────────────────────────────────┘
│          │ All Tests Pass                        │
│          ▼                                       │
│  ┌────────────────┐                              │
│  │ Launch Agent   │                              │
│  │ (Release)      │                              │
│  └────────────────┘                              │
│                                                  │
└──────────────────────────────────────────────────┘
```

## Value of Integration

| Without Knowledge Graph | With Knowledge Graph |
|------------------------|---------------------|
| Coding Agent sees only current file | Coding Agent understands full dependency tree |
| Bug Fix Agent guesses root cause | Bug Fix Agent traces call chain via graph |
| Design Agent relies on stale docs | Design Agent queries live architecture |
| QA Agent tests based on description | QA Agent identifies all affected paths |
| CI/CD Agent reviews in isolation | CI/CD Agent flags cross-module impacts |

## Shared Infrastructure

Both the Coding CI/CD pipeline and the Understand Anything analysis pipeline run on the same Bedrock AgentCore Runtime with Claude Agent SDK:

- **Same runtime** — Shared AgentCore infrastructure, consistent agent behavior
- **Same VPC** — Neptune accessible from all agents via private subnet
- **MCP tool pattern** — Neptune query exposed as MCP tool, reusable by any agent
- **Clear separation** — Preparation (build graph) is decoupled from Usage (query graph)
