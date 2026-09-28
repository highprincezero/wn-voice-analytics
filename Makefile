.PHONY: test lint sample up tf

test:
	cd backend && pytest

lint:
	ruff check backend frontend
	ruff format --check backend frontend

sample:
	python3 scripts/generate_sample_audio.py

up:
	docker compose up --build

tf:
	cd infra/terraform && terraform fmt -check -recursive && terraform init -backend=false && terraform validate
