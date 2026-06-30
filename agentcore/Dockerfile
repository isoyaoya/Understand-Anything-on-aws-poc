# === Stage 1: Node.js environment (build understand-anything plugin) ===
FROM public.ecr.aws/docker/library/node:22-slim AS builder

RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*
RUN npm install -g pnpm @anthropic-ai/claude-code

# Install and build understand-anything plugin
RUN claude plugin marketplace add Egonex-AI/Understand-Anything \
    && claude plugin install understand-anything --scope user
RUN cd /root/.claude/plugins/marketplaces/understand-anything \
    && pnpm install --ignore-scripts && pnpm build

# === Stage 2: Python runtime with venv (production) ===
FROM public.ecr.aws/docker/library/python:3.11-slim-bookworm

# Install system dependencies (Node.js, git, etc.)
RUN apt-get update && apt-get install -y curl git \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Copy compiled plugin from builder stage
COPY --from=builder /root/.claude /root/.claude

WORKDIR /app

# Create virtual environment
RUN python3 -m venv /app/.venv

# Install Python dependencies into venv and verify
COPY requirements.txt .
RUN /app/.venv/bin/pip install --no-cache-dir -r requirements.txt && \
    echo "[DEBUG] Installed package directory: gremlin_python (underscore)" && \
    ls /app/.venv/lib/python3.11/site-packages/ | grep gremlin && \
    echo "[DEBUG] Testing correct import with underscore:" && \
    /app/.venv/bin/python -c "import gremlin_python; print('[BUILD] ✅ gremlin_python (underscore) import OK')" && \
    /app/.venv/bin/python -c "from gremlin_python.driver import client; print('[BUILD] ✅ gremlin_python.driver import OK')"

# Activate venv by modifying PATH (for runtime)
ENV PATH="/app/.venv/bin:$PATH"

# Application code
COPY tools/neptune_http.py ./tools/
COPY tools/neptune_mcp_server.py ./tools/
COPY tools/neptune_writer.py ./tools/
COPY tools/s3_manager.py ./tools/
COPY main.py .
COPY CLAUDE.md .
COPY .bedrock_agentcore.yaml .

RUN mkdir -p /tmp/repos /tmp/workspace

ENV CLAUDE_CODE_USE_BEDROCK=1
ENV AWS_DEFAULT_REGION=us-east-1
ENV ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-20250514

EXPOSE 8080
# Use 'python' (not 'python3') to ensure venv's python is used
CMD ["python", "main.py"]
