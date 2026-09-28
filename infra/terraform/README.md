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

Resource group, VNet, an apps subnet delegated to Container Apps, a data subnet delegated to Postgres Flexible Server, private DNS zone `privatelink.postgres.database.azure.com`, Postgres 16 (`GP_Standard_D4ds_v5`, 128 GB, public access off), a ZRS storage account with a private `voice` container, Redis Standard C1, Key Vault (RBAC), Service Bus Standard with queues `transcription`, `llm-layer1`, `llm-layer2`, and `rollup`, Log Analytics, Application Insights, a Cognitive Services OpenAI account (`gpt-4o-transcribe`, `gpt-4.1-mini`) and a Content Safety account, a Container Apps environment, the API (min 2, max 8, external ingress on port 8000), the worker (min 1, max 20), the rollup job, and API Management Consumption.

## Global

One resource group in `global_location` holds a Front Door Standard profile. Each regional API hostname is an origin. Add a region by appending an object to `regions` with a non-overlapping CIDR. Remove a region by deleting that object. There is no shared database between them.
