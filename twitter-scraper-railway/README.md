# 🐦 Twitter/X Scraper — Railway Edition

Auto-scraping service with web dashboard. Deployed on Railway.

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
cd twitter-scraper-railway
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
In Railway dashboard → Variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `TWITTER_CT0` | ✅ | Twitter `ct0` cookie |
| `TWITTER_AUTH_TOKEN` | ✅ | Twitter `auth_token` cookie |
| `SCRAPE_INTERVAL_HOURS` | ❌ | Auto-scrape interval (default: 6) |
| `TELEGRAM_BOT_TOKEN` | ❌ | Telegram bot token |
| `TELEGRAM_CHAT_ID` | ❌ | Telegram chat ID |
| `PORT` | ❌ | Auto-set by Railway |

### 4. Get Cookies
1. Log into x.com in your browser
2. DevTools (F12) → Application → Cookies → x.com
3. Copy `ct0` and `auth_token` values

### 5. Add Volume (recommended)
Railway → Service → Settings → Add Volume
- Mount path: `/app/data`
- This persists your tweet database across redeploys

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Dashboard |
| `GET` | `/api/stats` | Service stats |
| `GET` | `/api/targets` | List tracked users |
| `POST` | `/api/targets?username=x` | Add user to track |
| `DELETE` | `/api/targets/{user}` | Remove user |
| `POST` | `/api/scrape/{user}?limit=0` | Scrape user now |
| `POST` | `/api/scrape-all` | Scrape all targets |
| `GET` | `/api/tweets/{user}?limit=100` | Get tweets |
| `GET` | `/api/download/{user}?fmt=txt` | Download as file |
| `GET` | `/api/log` | Scrape history |

## Local Dev
```bash
pip install -r requirements.txt
export TWITTER_CT0="your_ct0"
export TWITTER_AUTH_TOKEN="your_auth_token"
python main.py
# → http://localhost:8080
```
