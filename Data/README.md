# Data — Lakehouse Architecture

This directory keeps safe placeholders and metadata only. Raw cardholder data and generated lakehouse artifacts are intentionally excluded from Git and Docker build contexts.

## Local data policy

- Do **not** commit raw CSV files, Parquet outputs, validation reports, or other generated artifacts under `Data/`.
- Place private source data at `Data/bronze/02_fraudTest.csv` only in your local environment, or let `backend/seed.py` generate a synthetic demo dataset on first boot.
- Docker builds do not copy `Data/`; local runtime data is provided through the `./Data:/app/Data` volume in `docker-compose.yml`.

## Layers

```
Data/
├── bronze/        # Local-only raw input and Bronze outputs
├── silver/        # Local-only cleaned/transformed outputs
├── gold/          # Local-only validated/curated outputs
└── rejected/      # Local-only failed validation outputs
```

## Expected local files

When running the pipeline locally, the application may create:

- `Data/bronze/02_fraudTest.csv` — private raw input or synthetic seed data
- `Data/bronze/fraud_bronze.parquet`
- `Data/bronze/fraud_bronze.csv`
- `Data/bronze/ingestion_metadata.json`
- `Data/silver/fraud_silver.parquet`
- `Data/silver/cleaning_metadata.json`
- `Data/gold/fraud_gold.parquet`
- `Data/gold/validation_report.json`
- `Data/rejected/fraud_rejected.parquet`

These files are ignored because the dataset includes sensitive PII/PAN-like fields and derived artifacts can preserve that data.

## Data Dictionary

See [data_dictionary.md](../Docs/data_dictionary.md) for the full column reference.
