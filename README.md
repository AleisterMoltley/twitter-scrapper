# 🐦 Twitter/X Scraper — Railway Edition

Auto-scraping service with a web dashboard, deployed on Railway.

## Features

- **Web Dashboard** — Add targets, trigger scrapes, download results
- **Auto-Scraping** — Runs every N hours (configurable)
- **SQLite Storage** — All tweets persisted across deploys
- **Download** — Export as `.txt` or `.jsonl` per user
- **Telegram Alerts** — Optional notifications on scrape completion
- **REST API** — Full API for programmatic access

## Deploy to Railway

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "init twitter scraper"
gh repo create twitter-scraper --private --push
```

### 2. Deploy on Railway
- Go to [railway.app](https://railway.app)
- New Project → Deploy from GitHub Repo
- Select the repo

### 3. Set Environment Variables
In the Railway dashboard → Variables:

| Variable | Required | Description |
|---|---|---|
| `TWITTER_CT0` | ✅ | Twitter `ct0` cookie |
| `TWITTER_AUTH_TOKEN` | ✅ | Twitter `auth_token` cookie |
| `SCRAPE_INTERVAL_HOURS` | ❌ | Auto-scrape interval in hours (default: `6`) |
| `TELEGRAM_BOT_TOKEN` | ❌ | Telegram bot token |
| `TELEGRAM_CHAT_ID` | ❌ | Telegram chat ID |
| `PORT` | ❌ | Auto-set by Railway |

### 4. Get Twitter Cookies
1. Log into [x.com](https://x.com) in your browser
2. Open DevTools (`F12`) → Application → Cookies → `x.com`
3. Copy the values of `ct0` and `auth_token`

### 5. Add a Volume (recommended)
Railway → Service → Settings → Add Volume
- Mount path: `/app/data`
- This keeps your tweet database intact across redeploys

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web dashboard |
| `GET` | `/api/stats` | Service statistics |
| `GET` | `/api/targets` | List tracked users |
| `POST` | `/api/targets?username=x` | Add a user to track |
| `DELETE` | `/api/targets/{user}` | Remove a tracked user |
| `POST` | `/api/scrape/{user}?limit=0` | Scrape a user immediately |
| `POST` | `/api/scrape-all` | Scrape all tracked targets |
| `GET` | `/api/tweets/{user}?limit=100` | Retrieve tweets for a user |
| `GET` | `/api/download/{user}?fmt=txt` | Download tweets as a file (`txt` or `jsonl`) |
| `GET` | `/api/log` | View scrape history |

## Local Development

```bash
pip install -r requirements.txt
export TWITTER_CT0="your_ct0"
export TWITTER_AUTH_TOKEN="your_auth_token"
python main.py
# → http://localhost:8080
```

## Project Structure

```
.
├── main.py            # FastAPI application
├── requirements.txt   # Python dependencies
├── Dockerfile         # Container image definition
├── Procfile           # Process entrypoint (Railway / Heroku)
├── railway.toml       # Railway configuration
└── data/
    └── scraper.db     # SQLite database (auto-created)
```

## Tech Stack

- **[FastAPI](https://fastapi.tiangolo.com/)** — Web framework & REST API
- **[twscrape](https://github.com/vladkens/twscrape)** — Twitter scraping library
- **[APScheduler](https://apscheduler.readthedocs.io/)** — Background job scheduler
- **SQLite** — Lightweight persistent storage
- **[Railway](https://railway.app)** — Cloud deployment platform