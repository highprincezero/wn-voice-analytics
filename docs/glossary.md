# Glossary of artifacts

**Yes** means that context uses the artifact. A blank cell means it does not.

| Column | Context |
| --- | --- |
| local | `docker compose up` (`make up`). Celery, Redis, Postgres, Azurite, and Streamlit. |
| cloud (azure) | The multi-region stack in `infra/`. Service Bus, Container Apps, Azure Blob, Azure OpenAI, and Content Safety. |

This list is the repository root. A folder row means the whole folder. Files inside those folders are not listed. The Makefile is one root file with five scripts, so each target is its own row.

| name | local | cloud (azure) |
| --- | --- | --- |
| `.env.example` | yes | |
| `.github/` | | |
| `.gitignore` | yes | |
| `DEMO_SCRIPT.md` | yes | |
| `Makefile` `test` | | |
| `Makefile` `lint` | | |
| `Makefile` `sample` | | |
| `Makefile` `up` | yes | |
| `Makefile` `tf` | | |
| `PLAN_STATUS.md` | | |
| `README.md` | yes | yes |
| `SESSION_RECAP.md` | | |
| `backend/` | yes | yes |
| `docker-compose.override.yml` | yes | |
| `docker-compose.yml` | yes | |
| `docs/` | yes | yes |
| `frontend/` | yes | |
| `infra/` | | yes |
| `ruff.toml` | | |
| `samples/` | yes | |
| `scripts/` | | |

`Makefile` `tf` runs `terraform fmt`, `terraform init -backend=false`, and `terraform validate`. It does not apply the stack. `infra/` is the cloud provisioning.

`docker-compose.override.yml` is on this machine only. Compose loads it for local.
