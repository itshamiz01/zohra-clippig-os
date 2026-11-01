# Infrastructure boundary

`docker-compose.yml` is a development/staging scaffold, not production evidence.

- PostgreSQL and Redis bind only to localhost.
- API and web are exposed for local development; the worker has no public port.
- The API image includes FFmpeg/FFprobe and runs as a non-root user.
- API startup applies Alembic migrations; the Redis-backed worker waits for healthy dependencies.
- API and worker share a private local-storage volume only for this scaffold; render scratch space stays outside that volume.
- The worker declares PID, memory and CPU bounds in addition to child-process RLIMITs.
- Runtime secrets must come from an uncommitted `.env` or a secret manager.
- No external transcription/model/publishing provider is enabled by Compose.
- The global processing control remains paused until a platform administrator with owner authority records a reason to resume.
- Docker was unavailable in the Arena validation environment, so build, network, health, migration ordering and persistence behavior are **unverified** until exercised on a Docker host.

Production still needs a proven private object-store adapter (S3 is not implemented), managed PostgreSQL/Redis, TLS/ingress, media sandboxing, malware scanning, backups, monitoring, retention/deletion workflows and the later Phase 11 gates.
