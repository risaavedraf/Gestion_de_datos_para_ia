# Data — Lakehouse Architecture

## Layers

```
Data/
├── bronze/        # Raw data (immutable)
│   ├── 02_fraudTest.csv
│   ├── fraud_bronze.parquet
│   └── ingestion_metadata.json
├── silver/        # Cleaned & transformed
│   ├── fraud_silver.parquet
│   └── cleaning_metadata.json
├── gold/          # Validated & curated
│   ├── fraud_gold.parquet
│   └── validation_report.json
└── rejected/      # Failed validation
    └── fraud_rejected.parquet
```

## Bronze Layer
- Source: `02_fraudTest.csv` (555,719 rows, 23 columns)
- Process: Read CSV, compute SHA256 checksum, save as Parquet
- Output: Immutable copy + metadata

## Silver Layer
- Input: Bronze Parquet
- Process: Drop technical columns, cast types, normalize strings, create derived columns
- Derived: `trans_hour`, `trans_day_of_week`, `trans_month`, `age_at_transaction`, `distance_km`, `cc_num_masked`
- Output: Cleaned dataset

## Gold Layer
- Input: Silver Parquet
- Process: Structural validation (columns, types, ranges) + Semantic validation (business rules)
- Output: Valid records + rejected records with reasons

## Data Dictionary

See [data_dictionary.md](../Docs/data_dictionary.md) for full column reference.

## Dataset Statistics

- Records: 555,719
- Columns: 23 (original) + 6 (derived)
- Fraud rate: 0.386% (2,145 transactions)
- Date range: 2020-06-21 to 2020-12-31
- Amount range: $1.00 to $22,768.11
