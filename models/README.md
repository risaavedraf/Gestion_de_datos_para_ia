# Models — ML Pipeline

## Structure

```
models/
├── best_model.joblib          # Serialized best model
├── model_metadata.json        # Model type, params, CV scores
└── evaluation_report.json     # Full evaluation metrics
```

## Model Training

Three models are trained and compared:

| Model | Description |
|-------|-------------|
| LogisticRegression | Linear baseline |
| RandomForest | Ensemble of decision trees |
| XGBoost | Gradient boosting (usually best) |

Selection criterion: **F1-Score** (balances precision and recall for imbalanced data).

## Class Imbalance

The dataset has 0.386% fraud rate. Models use `class_weight='balanced'` to handle this.

## Usage

```bash
# Train models
python main.py --train

# Or via API
POST /api/model/train
```

## Pre-trained Model

The repository includes a pre-trained model (`best_model.joblib`) for immediate use during demos.
