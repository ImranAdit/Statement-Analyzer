# Adit Pay — Statement Analyser

Upload a bank/merchant statement (PDF or scanned image), review extracted figures, and get an instant fee comparison against Adit Pay pricing.

## Pricing Logic (from spreadsheet)

| Mode | Rate | Auth Fee |
|------|------|----------|
| Card Present | 2.25% | $0.20/trn |
| Online (Card Not Present) | 2.90% | $0.30/trn |

The tool replicates both sheets from the spreadsheet:
- **Template mode** — splits transactions by a card-present % (default 90/10)
- **Card Present Only** — all transactions at the in-person rate

## Local Development

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev          # http://localhost:5173 (proxies /api → :8000)
```

## Deploy to Railway

1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select the repo — Railway auto-detects the Dockerfile
4. Click **Deploy** — you'll get a public URL in ~2 minutes

No environment variables are needed by default.

## Project Structure

```
statement-analyzer/
├── backend/
│   ├── main.py           # FastAPI app + calculation logic
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx       # Full UI
│   │   ├── main.jsx
│   │   └── index.css
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── Dockerfile
├── railway.toml
└── README.md
```
