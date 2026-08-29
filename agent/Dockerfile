# Deployment-owned packaging for the owner-supplied prover agent. The source,
# queue protocol, heartbeat protocol, and runtime behavior remain owned by
# prover-node/agent; this image only turns those reviewed bytes into a minimal
# non-root runtime artifact.
FROM node:22-bookworm@sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a AS build

WORKDIR /source/prover-node
RUN corepack enable && corepack prepare pnpm@10.13.1 --activate
COPY prover-node/package.json prover-node/pnpm-workspace.yaml prover-node/pnpm-lock.yaml prover-node/tsconfig.base.json ./
COPY prover-node/agent/package.json prover-node/agent/tsconfig.json ./agent/
COPY prover-node/agent/src ./agent/src
COPY prover-node/agent/liveness-capability.json ./agent/liveness-capability.json
COPY prover-node/agent/trace-capability.json ./agent/trace-capability.json

ARG AGENT_SOURCE_SHA256
ARG AGENT_CAPABILITY_SHA256
ARG AGENT_TRACE_CAPABILITY_SHA256
RUN test -n "$AGENT_SOURCE_SHA256" && test -n "$AGENT_CAPABILITY_SHA256" \
 && test -n "$AGENT_TRACE_CAPABILITY_SHA256" \
 && actual_source="$(find agent/src -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')" \
 && test "$actual_source" = "$AGENT_SOURCE_SHA256" \
 && echo "$AGENT_CAPABILITY_SHA256  agent/liveness-capability.json" | sha256sum -c - \
 && echo "$AGENT_TRACE_CAPABILITY_SHA256  agent/trace-capability.json" | sha256sum -c -
RUN pnpm install --frozen-lockfile --filter zkdeal-prover-agent...
RUN pnpm --filter zkdeal-prover-agent run build
# Bundle the typed owner entrypoint so the runtime contains neither a package
# manager nor test/development dependencies. esbuild is locked transitively by
# the owner's direct tsx dependency; invoke that exact installed artifact.
RUN esbuild_bin="$(find node_modules/.pnpm -path '*/node_modules/esbuild/bin/esbuild' -type f -print -quit)" \
 && test -n "$esbuild_bin" \
 && mkdir -p /runtime/agent \
 && "$esbuild_bin" agent/src/agent.ts --bundle --platform=node --format=esm \
      --target=node22 --outfile=/runtime/agent/agent.js

FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

ARG AGENT_SOURCE_SHA256
ARG AGENT_CAPABILITY_SHA256
ARG AGENT_TRACE_CAPABILITY_SHA256
LABEL org.opencontainers.image.title="zkdeal-prover-agent" \
      org.opencontainers.image.description="Owner prover agent packaged by cloud-deployer-infra" \
      org.zkdeal.owner.source.sha256="$AGENT_SOURCE_SHA256" \
      org.zkdeal.owner.liveness-capability.sha256="$AGENT_CAPABILITY_SHA256" \
      org.zkdeal.owner.trace-capability.sha256="$AGENT_TRACE_CAPABILITY_SHA256" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
COPY --from=build /runtime/agent ./agent
COPY --from=build /source/prover-node/agent/liveness-capability.json ./capabilities/liveness-capability.json
COPY --from=build /source/prover-node/agent/trace-capability.json ./capabilities/trace-capability.json
COPY LICENSE /LICENSE
RUN groupadd --gid 10001 zkdeal-agent \
 && useradd --system --no-create-home --uid 10001 --gid 10001 zkdeal-agent \
 && chown -R 10001:10001 /app
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD ["sh", "-ec", "kill -0 1"]
CMD ["node", "/app/agent/agent.js"]
