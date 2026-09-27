# Container deployment

Container configs for running the claude-usage dashboard in Docker or
Podman, with the host's ~/.claude directory bind-mounted in so the
container sees the same transcripts and SQLite DB as the host CLI.

## Layout

| File                  | Purpose                                                                |
| --------------------- | ---------------------------------------------------------------------- |
| Dockerfile            | Multi-stage build, python:3.12-slim, non-root user, /api/health probe. |
| Containerfile         | Identical to Dockerfile -- podman build picks it up by default.        |
| docker-compose.yml    | Single-service compose for Docker Engine / Docker Desktop.             |
| podman-compose.yml    | Same service for podman-compose, with userns_mode: keep-id.            |
| .dockerignore         | Keeps tests, caches, docs, and editor cruft out of the build context.  |

## Why the bind mount

The container's HOME is /data, so mounting ~/.claude at
/data/.claude is what makes the in-container CLI behave like the host
CLI: transcripts are read from /data/.claude/projects/**/*.jsonl and
the SQLite DB lives at /data/.claude/usage.db. Without the mount the
dashboard would start with an empty DB and rebuild it from nothing.

## Build

From the repo root:

```sh
# Docker
docker build -f deploy/Dockerfile -t claude-usage:latest .

# Podman (uses Containerfile by default, hence no -f needed)
podman build -t localhost/claude-usage:latest -f deploy/Containerfile .
```

## Run -- Docker Compose

```sh
# Match host ownership on the bind mount so files written from inside the
# container stay owned by your user on the host.
UID=$(id -u) GID=$(id -g) docker compose -f deploy/docker-compose.yml up -d
```

Open http://127.0.0.1:8090.

## Run -- Podman Compose

```sh
podman-compose -f deploy/podman-compose.yml up -d
```

userns_mode: keep-id tells rootless podman to map the container's app
user (uid 1000) back to your host user, so writes to ~/.claude/usage.db
land as you rather than as a remapped subuid.

## Run without compose

```sh
docker run --rm -p 8090:8090 \
    -v "$HOME/.claude:/data/.claude" \
    --user "$(id -u):$(id -g)" \
    --name claude-usage \
    claude-usage:latest

# Rootless podman equivalent:
podman run --rm -p 8090:8090 \
    -v "$HOME/.claude:/data/.claude" \
    --userns=keep-id \
    --name claude-usage \
    localhost/claude-usage:latest
```

## Triggering a scan

The serve command does not rebuild the DB; if you want to force a fresh
scan against the bind-mounted transcripts, exec into the running container:

```sh
docker compose -f deploy/docker-compose.yml exec claude-usage python cli.py scan
# or
podman-compose -f deploy/podman-compose.yml exec claude-usage python cli.py scan
```

## Health check

Both the Dockerfile and the compose files poll GET /api/health. The
endpoint returns 200 once the HTTP server is up; the compose healthcheck
runs every 30s with a 10s start grace period.

## Rootless Podman notes

- userns_mode: keep-id requires podman 3+ and a working /etc/subuid
  / /etc/subgid entry for your user. Most modern distros set this up
  during package install.
- If you skip keep-id, the bind mount will look root-owned from inside
  the container but will be written as a remapped subuid on the host --
  which is awkward to clean up later.
- Port 8090 is not privileged, so no --cap-add / sysctl tweaks needed.
