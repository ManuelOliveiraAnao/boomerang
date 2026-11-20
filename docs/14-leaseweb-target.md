# 14 · Leaseweb target files (owner: B)

What `generators/leaseweb.py` writes to `runs/<id>/leaseweb/`. These are real, ready-to-use files for a Leaseweb server (Public Cloud instance, VPS or dedicated server running Ubuntu 24.04), not a plan.

## 1. Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | The whole stack (§2) |
| `.env.example` | Non-secret settings: names, ports, endpoints. Committed with the deliverable |
| `secrets.env` | Generated passwords and keys (S4 in [13](13-security.md)). `chmod 600`, never committed, never sent to the LLM |
| `schema.sql` | `sovereign_sdk.kv.ddl()` for every kv table; mounted into Postgres `docker-entrypoint-initdb.d` |
| `rabbitmq/definitions.json` | vhost `shoplite`, user with permissions only on it, quorum queue `order-events` with `x-delivery-limit: 3` and dead-letter exchange → `order-events-dlq` |
| `seaweedfs/s3.json` | One S3 identity with actions limited to bucket `receipts` |
| `cloud-init.yaml` | For a fresh Leaseweb instance: install Docker, create `shoplite` user, open only 22/80/443 with `ufw`, copy files, `docker compose up -d` |
| `deploy.sh` | `rsync` this folder to `SERVER` over SSH, then `docker compose pull && docker compose up -d --wait` and a health check. Takes `SERVER=user@host` |
| `backup/pg_backup.sh` + compose `backup` service | Nightly `pg_dump` to object storage (SeaweedFS bucket or Leaseweb Object Storage); keeps 7 days |
| `DEPLOY.md` | Step-by-step: order server → cloud-init or `deploy.sh` → DNS → TLS (Caddy) → verify → cut-over → rollback. Lists the 🔴 items that need a human |
| `app/` | The de-locked app + `sovereign_sdk/` + original `tests/` + `Dockerfile` + `requirements.txt` |
| `terraform/` (stretch S1) | Leaseweb Public Cloud instance via Leaseweb's official Terraform provider. Check resource names in the provider docs before writing; skip if unsure |

## 2. `docker-compose.yml` services

| Service | Image | Notes |
|---|---|---|
| `postgres` | `postgres:16-alpine` | volume, healthcheck `pg_isready`, init script creates app user/db with least privilege + `schema.sql` |
| `rabbitmq` | `rabbitmq:4-management-alpine` | loads `definitions.json`; healthcheck `rabbitmq-diagnostics -q ping`; management UI bound to `127.0.0.1` only |
| `seaweedfs` | `chrislusf/seaweedfs` | `server -s3 -s3.config=/etc/seaweedfs/s3.json`; healthcheck on the S3 port. Omit this service when `S3_PROVIDER=leaseweb` (Leaseweb Object Storage endpoint + keys instead: config only) |
| `api` | built from `app/Dockerfile` | `uvicorn app.main:app --host 0.0.0.0 --port 8080`; `depends_on: condition: service_healthy` |
| `worker` | same image | `python -m app.worker` |
| `backup` | `postgres:16-alpine` | cron-like loop running `pg_backup.sh` |

Laptop verification uses the **same file** with host ports on `127.0.0.1` (5432, 5672, 15672, 8333, 8080). On the server, only the reverse proxy is exposed.

## 3. Verification (A's verifier runs these)
1. `docker compose config -q` · JSON/YAML parse · `bash -n deploy.sh` · `cloud-init schema --config-file cloud-init.yaml` if `cloud-init` is available (skip with a note otherwise).
2. `docker compose up -d --build --wait`.
3. Original tests 7/7 against `127.0.0.1` ports.
4. `GET http://127.0.0.1:8080/health` → 200.
5. Scope check: try to access a different bucket with the app's S3 key → must be denied (stretch S3).

## 4. Real Leaseweb (only with organizer access, Q5)
Order the smallest suitable instance, run `deploy.sh` **before** the demo, record the health check and the URL. Never deploy live on stage.
