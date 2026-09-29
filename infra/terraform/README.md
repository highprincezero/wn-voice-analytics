> Applicable only for cloud provisioning. This file is used only when deployed to the cloud with a multi-region deployment.

# Terraform

Azure layout for N regions. `N` is `length(var.regions)`. The default list is eastus (`eus`) and westeurope (`weu`).

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

Those three commands do not need Azure credentials. `terraform plan` and `apply` do.

## Apply

```bash
cp terraform.tfvars.example terraform.tfvars
# set db_admin_password and jwt_secret
terraform init
terraform plan
```

Required inputs with no default: `db_admin_password`, `jwt_secret`. Change `name_prefix` before a real apply. It is used in globally unique names (storage account, Key Vault).

`api_image` and `worker_image` default to a GHCR name. Push the backend image there, or point both variables at your registry, before the Container Apps can start. The worker and the rollup job use the same image as the API, with different commands:

- API: image default command (`uvicorn`)
- Worker: `python -m app.jobs.service_bus_worker`
- Rollup job: `python -m app.jobs.run_scheduled_rollup` on cron `*/15 * * * *`

Secrets (JWT, database URL, storage connection string, Service Bus, OpenAI key, Content Safety key) are written to Key Vault and referenced by the Container Apps. Do not commit `terraform.tfvars`.

## What each region gets

Resource group, VNet, an apps subnet delegated to Container Apps (`/23`, the consumption-only minimum), a data subnet delegated to Postgres Flexible Server, private DNS zone `privatelink.postgres.database.azure.com`, Postgres 16 (`GP_Standard_D4ds_v5`, 128 GB, public access off, zone-redundant high availability in zones 1 and 2, geo-redundant backups), a GZRS storage account with a private `voice` container, Redis Standard C1, Key Vault (RBAC), Service Bus Standard with queues `transcription`, `llm-layer1`, `llm-layer2`, and `rollup`, Log Analytics, Application Insights, a Cognitive Services OpenAI account (`gpt-4o-transcribe`, `gpt-5-mini`) and a Content Safety account, a zone-redundant Container Apps environment, the API (min 2, max 8, external ingress on port 8000, allowlisted to API Management), the worker (min 1, max 20), the rollup job, and API Management Basic.

API Management publishes the API at the gateway root and forwards to the Container App. The policy requires the Front Door id header, validates the HS256 JWT on authenticated routes, and rate-limits by the `sub` claim (`apim_user_rate_limit` per `apim_rate_window_seconds`, default 120 per 60). Sign-up, login, health, meta, and the prompt catalog skip the JWT check and are limited per source IP. The JWT named value is the base64 form of `jwt_secret`, which is what the gateway expects for HMAC. The API container gets the same numeric limit in Redis.

`rate-limit-by-key` is not supported on the Consumption tier. Basic is the tier in this module. Service Bus and Redis stay on Standard. None of those three move to Premium for zones. See `docs/scaling.md`.

Regions must support availability zones 1 and 2, GZRS, and zone-redundant Postgres HA together with geo-redundant backup. The default regions, eastus and westeurope, do.

## Global

One resource group in `global_location` holds a Front Door Standard profile. Each origin is the regional API Management gateway hostname, with a health probe on `/api/v1/health`. Add a region by appending an object to `regions` with a non-overlapping CIDR. Remove a region by deleting that object. There is no shared database between them.
