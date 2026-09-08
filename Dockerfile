# ==============================================================================
# VoiceForge StudyBuddy — Multi-Service Dockerfile
# Provides complete environment (Python 3.11 + Node.js 20) with zero local setup.
# ==============================================================================

FROM python:3.11-slim-bookworm

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    NODE_ENV=development

# Install system dependencies & Node.js 20
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    build-essential \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Step 1: Install Python dependencies (cached layer)
COPY agent/requirements.txt ./agent/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r agent/requirements.txt

# Step 2: Install Node.js frontend dependencies (cached layer)
COPY frontend/package*.json ./frontend/
RUN cd frontend && npm install

# Step 3: Copy full project
COPY . .

# Ensure entrypoint is executable and has UNIX line endings
RUN chmod +x docker-entrypoint.sh \
    && sed -i 's/\r$//' docker-entrypoint.sh

# Expose Web Frontend (5173) and Token Server (7880)
EXPOSE 5173 7880

ENTRYPOINT ["/bin/bash", "./docker-entrypoint.sh"]
CMD ["all"]
