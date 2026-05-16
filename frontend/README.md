# Frontend — Dashboard SPA

## Overview

Single Page Application with 9 tabs showing the complete DataOps pipeline.

## Sections

| Tab | Content |
|-----|---------|
| **Overview** | Project summary, quick KPIs, action buttons |
| **Dataset** | Statistics, fraud distribution chart, sample data |
| **Architecture** | Bronze/Silver/Gold diagram, justification |
| **Pipeline** | Interactive execution, status per stage, logs |
| **PMBOK** | Methodology explanation, WBS, timeline |
| **Security** | Legal compliance, encryption, masking |
| **CI/CD** | GitHub Actions → Render flow diagram |
| **Model** | Metrics, confusion matrix, ROC curve, live predict |
| **Demo** | Run pipeline with sample, see results live |

## Tech Stack

- HTML5
- CSS3 (dark theme, responsive grid)
- JavaScript (vanilla, no framework)
- Chart.js (visualizations)

## API Integration

All data fetched from backend API at `/api/*` endpoints using relative paths.

## Theme

- Background: `#0f172a`
- Cards: `#1e293b`
- Accent: `#3b82f6`
- Text: `#e2e8f0`
- Success: `#22c55e`
- Warning: `#f59e0b`
- Danger: `#ef4444`
