# AI-Driven Coding CI/CD Workflow

## Overview

An end-to-end software delivery pipeline powered by Claude Agent SDK on Bedrock AgentCore Runtime. Multiple specialized agents collaborate through MCP tool integrations (GitLab, Jira, etc.) to automate the journey from requirement to production release.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    Bedrock AgentCore Runtime (Claude Agent SDK)                   │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                         Agent Orchestration Layer                            │ │
│  │                                                                             │ │
│  │   Phase 1: Requirements            Phase 2: Design & Implementation         │ │
│  │   ┌──────────────────┐             ┌──────────────────┐                     │ │
│  │   │  PRD Review Agent │────────────▶│ Design Agent     │                     │ │
│  │   │  (需求分析/拆解)   │  Approval   │ (架构/技术方案)   │                     │ │
│  │   └──────────────────┘             └────────┬─────────┘                     │ │
│  │                                             │ Approval                       │ │
│  │                                             ▼                                │ │
│  │   Phase 3: Coding                  ┌──────────────────┐                     │ │
│  │   ┌──────────────────┐             │  Coding Agent    │                     │ │
│  │   │  Bug Fix Agent   │◀── Bug ─────│  (代码生成/单元测试)│                     │ │
│  │   │  (缺陷修复)       │    Report   └────────┬─────────┘                     │ │
│  │   └───────┬──────────┘                      │ Commit & Push                 │ │
│  │           │ Fix                              ▼                               │ │
│  │           │              Phase 4:   ┌──────────────────┐                     │ │
│  │           └─────────────────────────▶  CI/CD Agent     │                     │ │
│  │                                     │  (Review/Build/  │                     │ │
│  │                                     │   Test/Deploy)   │                     │ │
│  │                                     └────────┬─────────┘                     │ │
│  │                                              │ Deploy to Staging             │ │
│  │                                              ▼                               │ │
│  │   Phase 5: QA & Release            ┌──────────────────┐                     │ │
│  │   ┌──────────────────┐  Test Cases │  QA Agent        │                     │ │
│  │   │  Launch Agent    │◀────────────│  (测试设计/集成测试)│                     │ │
│  │   │  (发布上线)       │  Approved   └──────────────────┘                     │ │
│  │   └──────────────────┘                                                      │ │
│  │                                                                             │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                         MCP Tool Integrations                               │ │
│  │                                                                             │ │
│  │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │ │
│  │   │  GitLab  │  │   Jira   │  │ SonarQube│  │ Artifact │  │  Slack   │   │ │
│  │   │ (SCM/CI) │  │ (Issues) │  │ (Quality)│  │ Registry │  │ (Notify) │   │ │
│  │   └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │ │
│  │                                                                             │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Workflow Sequence

```
PRD Document ──▶ Review & Analysis ──▶ Technical Design ──▶ Code Generation
     │                                                           │
     │                                                           ▼
     │              ┌─────── Bug Fix ◀── Bug Report ◀── CI/CD Pipeline
     │              │                                       (Review + Build + Test)
     │              ▼                                            │
     │         Commit Fix                                       ▼
     │              │                                    Deploy to Staging
     │              └──────────────────────────────▶          │
     │                                                        ▼
     │                                              Integration Testing
     │                                                        │
     │                                                        ▼
     └─── Human Checkpoints ───────────────────────▶  Release to Production
          (Design Approval,
           Test Report Review)
```

## Agent Responsibilities

| Agent | Role | MCP Tools Used |
|-------|------|----------------|
| PRD Review Agent | Parse requirements, identify risks, break into tasks | Jira (create stories) |
| Design Agent | Generate technical design, API specs, DB schema | GitLab (create branch) |
| Coding Agent | Implement code, write unit tests | GitLab (commit/push) |
| CI/CD Agent | Code review, run tests, build, deploy to staging | GitLab CI, SonarQube |
| Bug Fix Agent | Analyze failures, generate fixes, re-submit | GitLab, Jira (update) |
| QA Agent | Design test cases, run integration/E2E tests | Test frameworks |
| Launch Agent | Final deployment, release notes, notifications | GitLab CD, Slack |

## Human-in-the-Loop Checkpoints

Three approval gates ensure quality and control:

1. **Design Approval** — Architect reviews technical design before coding begins
2. **Test Report Review** — QA lead validates integration test results
3. **Release Authorization** — Product owner approves production deployment

## Key Characteristics

- **Multi-Agent Orchestration** — Specialized agents for each phase, coordinated by the runtime
- **Autonomous with Guardrails** — Agents execute independently between human checkpoints
- **Self-Healing Loop** — Bug Fix Agent automatically triggered on CI/CD failures
- **Full Traceability** — Every action logged via MCP integrations (GitLab commits, Jira updates)
