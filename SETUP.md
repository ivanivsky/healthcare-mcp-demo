# My Health Access Setup Guide

This guide walks you through setting up the My Health Access demo application on your local machine.

## Prerequisites

- **Python 3.12 or 3.13** (Python 3.14 is not yet supported due to pydantic-core compatibility)
- **Google Cloud authentication** (uses Application Default Credentials)
  - Run `gcloud auth application-default login` to authenticate locally
  - On Cloud Run, uses Workload Identity automatically

## Quick Start

### 1. Create and activate a virtual environment

```bash
# Create virtual environment
python -m venv venv

# Activate it
# On macOS/Linux:
source venv/bin/activate

# On Windows:
.\venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set required environment variables

**Option A: Use a .env file (recommended)**

```bash
# Copy the example file
cp .env.example .env

# Edit .env with your actual values:
# GOOGLE_CLOUD_PROJECT=healthcare-demo-app
# FIREBASE_API_KEY=AIzaSyC...
# FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
```

**Option B: Export directly**

```bash
# On macOS/Linux:
export GOOGLE_CLOUD_PROJECT="healthcare-demo-app"
export GOOGLE_CLOUD_LOCATION="us-central1"  # optional, defaults to us-central1
export FIREBASE_API_KEY="AIzaSyC..."
export FIREBASE_AUTH_DOMAIN="your-project.firebaseapp.com"
export FIREBASE_PROJECT_ID="your-project-id"  # optional
```

**Getting Firebase credentials:**
1. Go to [Firebase Console](https://console.firebase.google.com)
2. Select your project (or create one)
3. Go to Project Settings > General
4. Under "Your apps", find your web app or create one
5. Copy the `apiKey` and `authDomain` from the config snippet

**Verify config is set correctly:**
When the backend starts, you should see:
```
FIREBASE_CONFIG: ready (apiKey=AIzaSyC..., authDomain=your-project.firebaseapp.com)
```

If config is missing, you'll see:
```
FIREBASE_CONFIG: missing [FIREBASE_API_KEY, FIREBASE_AUTH_DOMAIN] - frontend will show config error
```

### 4. Initialize the database with sample data

```bash
python scripts/seed_database.py
```

This creates `data/health_advisor.db` with 5 fake patients and their health records.

### 5. Start the MCP Server (Terminal 1)

```bash
python mcp_server/server.py
```

The MCP server will start on port 8001 with endpoints:
- SSE connection: `http://localhost:8001/sse`
- Messages: `http://localhost:8001/messages/`
- Health check: `http://localhost:8001/health`

### 6. Start the Backend API (Terminal 2)

**Quick start with run.sh:**

```bash
# Loads .env automatically and starts backend
./run.sh
```

**Or manually:**

```bash
python -m backend.main
```

The backend API will start on port 8080.

### 7. Open the application

Open your browser and navigate to: **http://localhost:8080**

## Architecture Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│    Frontend     │────▶│  FastAPI Backend │────▶│   MCP Server    │
│  (Port 8080)    │     │   (Port 8080)   │     │   (Port 8001)   │
│                 │     │                 │     │                 │
│  - Chat UI      │     │  - MCP Client   │     │  - PHI Tools    │
│  - Debug View   │     │  - Gemini Agent │     │  - PostgreSQL   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## Configuration

All settings are in `config.yaml`:

- **MCP transport**: Change between `stdio`, `sse`, or `streamable-http`
- **Security controls**: Toggle authentication, validation, etc.
- **Logging**: Adjust verbosity and debug panel settings

## Ports Used

| Component   | Port | Endpoints                                |
|------------|------|------------------------------------------|
| Backend    | 8080 | `/` (UI), `/api/*`, `/debug`             |
| MCP Server | 8001 | `/sse`, `/messages/`, `/health`          |

## Test Patients

The database is seeded with 5 patients for different test scenarios:

1. **Margaret Chen** - Complex case with multiple chronic conditions
2. **James Wilson** - Healthy baseline with minimal history
3. **Sofia Rodriguez** - Active cancer treatment
4. **Robert Thompson** - Insurance issues, gaps in care
5. **Emily Nakamura** - Sensitive diagnoses (for privacy testing)

## Troubleshooting

### Python 3.14 errors / pydantic-core build failures

Python 3.14 is too new - `pydantic-core` doesn't have pre-built wheels for it yet. Use Python 3.12 or 3.13 instead:

```bash
# Check your Python version
python --version

# If using pyenv, switch to 3.12 or 3.13
pyenv install 3.12
pyenv local 3.12
```

### Dependency version conflicts during pip install

If you see errors about incompatible versions of `pydantic` or `pydantic-settings`, the `requirements.txt` has been updated to fix these. Make sure you have the latest version of the file and try again:

```bash
pip install -r requirements.txt
```

The MCP SDK 1.2.0 requires `pydantic>=2.10.1` and `pydantic-settings>=2.6.1`.

### "MCP server not connected"

Make sure the MCP server is running in a separate terminal before starting the backend.

### "Google Cloud authentication errors"

Ensure you've authenticated with Google Cloud:
```bash
gcloud auth application-default login
```

### Database errors

Delete `data/health_advisor.db` and re-run the seed script:

```bash
rm data/health_advisor.db
python scripts/seed_database.py
```

## Debug View

Access the debug panel at: **http://localhost:8080/debug**

This shows:
- MCP tool calls (requests/responses)
- Claude API interactions
- Agent reasoning flow
- Current configuration

## Stopping the Application

1. Press `Ctrl+C` in each terminal to stop the servers
2. Deactivate the virtual environment: `deactivate`
