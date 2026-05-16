# CI/CD Pipeline

## Workflows

### ci.yml — Continuous Integration
- Triggers on push/PR to main
- Sets up Python 3.11
- Installs dependencies
- Runs pytest
- Checks imports

### deploy.yml — Deployment
- Triggers on push to main (after CI passes)
- Triggers Render deploy hook
- Render rebuilds and redeploys the Docker container

## Setup

1. Create Render Web Service connected to GitHub repo
2. Get Deploy Hook URL from Render Settings
3. Add `RENDER_DEPLOY_HOOK` as GitHub Secret
4. Push to main → auto-deploy
