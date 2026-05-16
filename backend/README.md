# Backend — FastAPI Application

## Overview

FastAPI application serving the DataOps pipeline and ML model.

## Structure

```
backend/
├── app.py                  # FastAPI application with 25 endpoints
├── config/
│   ├── settings.py         # Centralized configuration
│   ├── logging_config.py   # JSON logging setup
│   └── schema.sql          # PostgreSQL DDL reference
└── src/
    ├── ingestion.py        # Bronze: CSV → Parquet + metadata
    ├── cleaning.py         # Silver: transform, normalize, derive
    ├── validation.py       # Gold: structural + semantic rules
    ├── loader.py           # PostgreSQL: create tables, insert, dedup
    ├── features.py         # ML: feature engineering
    ├── model_train.py      # ML: train 3 models, select best
    ├── model_evaluate.py   # ML: metrics, confusion matrix, ROC
    ├── model_predict.py    # ML: single + batch prediction
    └── utils.py            # Helpers: masking, haversine, checksum
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/api/pipeline/run` | Run full pipeline |
| POST | `/api/pipeline/run/{stage}` | Run individual stage |
| GET | `/api/pipeline/status` | Pipeline layer status |
| GET | `/api/pipeline/logs` | Recent execution logs |
| GET | `/api/dataset/stats` | Dataset statistics |
| GET | `/api/dataset/sample` | Sample rows |
| GET | `/api/dataset/fraud-dist` | Fraud distribution |
| GET | `/api/dataset/dictionary` | Data dictionary |
| GET | `/api/kpis` | Pipeline KPIs |
| GET | `/api/validation/report` | Validation report |
| GET | `/api/validation/rejected` | Rejected records |
| POST | `/api/model/train` | Train ML models |
| GET | `/api/model/metrics` | Model evaluation metrics |
| POST | `/api/model/predict` | Predict single transaction |
| POST | `/api/model/predict-batch` | Predict batch |
| GET | `/api/model/feature-importance` | Feature importance |

## Running

```bash
# Direct
python main.py --serve

# Docker
docker-compose up -d
```
