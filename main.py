"""
Twitter/X Scraper — Railway Edition
====================================
FastAPI service with:
- Web dashboard to manage scrape jobs
- REST API to trigger scrapes
- Scheduled auto-scraping (cron)
- Download scraped tweets as .txt / .json
- Telegram notifications (optional)

Env vars (Railway):
  TWITTER_CT0          - Twitter ct0 cookie
  TWITTER_AUTH_TOKEN   - Twitter auth_token cookie
  TELEGRAM_BOT_TOKEN   - (optional) Telegram bot token
  TELEGRAM_CHAT_ID     - (optional) Telegram chat ID
  SCRAPE_INTERVAL_HOURS - Auto-scrape interval (default: 6)
  PORT                 - Server port (Railway sets this)
"""

import asyncio
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, FileResponse
from twscrape import API, gather

# ── Config ──────────────────────────────────────────────────────────────────
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "scraper.db"

TWITTER_CT0 = os.environ.get("TWITTER_CT0", "")
TWITTER_AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL_HOURS", "6"))
PORT = int(os.environ.get("PORT", "8080"))

scheduler = AsyncIOScheduler()


# ── Database ────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS targets (
            username TEXT PRIMARY KEY,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_scraped TEXT,
            tweet_count INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tweets (
            tweet_id TEXT PRIMARY KEY,
            username TEXT,
            date TEXT,
            text TEXT,
            likes INTEGER DEFAULT 0,
            retweets INTEGER DEFAULT 0,
            replies INTEGER DEFAULT 0,
            url TEXT,
            scraped_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            started_at TEXT,
            finished_at TEXT,
            new_tweets INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_user ON tweets(username)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_date ON tweets(date DESC)")
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Twitter API Setup ───────────────────────────────────────────────────────
async def get_twitter_api() -> API:
    api = API()
    cookies = f"ct0={TWITTER_CT0};auth_token={TWITTER_AUTH_TOKEN}"
    try:
        await api.pool.add_account(
            "scraper_account", "x", "x@x.com", "x", cookies=cookies
        )
    except Exception:
        pass  # account already exists
    await api.pool.login_all()
    return api


# ── Telegram ────────────────────────────────────────────────────────────────
async def send_telegram(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10,
            )
    except Exception as e:
        print(f"[TG] Error: {e}")


# ── Scraper Logic ──────────────────────────────────────────────────────────
async def scrape_user(username: str, limit: int | None = None) -> dict:
    """Scrape tweets from a user. Returns stats dict."""
    db = get_db()

    # Log start
    db.execute(
        "INSERT INTO scrape_log (username, started_at, status) VALUES (?, ?, 'running')",
        (username, datetime.now(timezone.utc).isoformat()),
    )
    log_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()

    try:
        api = await get_twitter_api()
        user = await api.user_by_login(username)
        if not user:
            raise Exception(f"User @{username} not found")

        tweets = await gather(api.user_tweets(user.id, limit=limit))

        new_count = 0
        for tweet in tweets:
            try:
                db.execute(
                    """INSERT OR IGNORE INTO tweets
                       (tweet_id, username, date, text, likes, retweets, replies, url)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(tweet.id),
                        username.lower(),
                        tweet.date.isoformat() if tweet.date else None,
                        tweet.rawContent,
                        tweet.likeCount,
                        tweet.retweetCount,
                        tweet.replyCount,
                        tweet.url,
                    ),
                )
                if db.execute("SELECT changes()").fetchone()[0] > 0:
                    new_count += 1
            except Exception:
                continue

        total = db.execute(
            "SELECT COUNT(*) FROM tweets WHERE username = ?", (username.lower(),)
        ).fetchone()[0]

        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE targets SET last_scraped = ?, tweet_count = ? WHERE username = ?",
            (now, total, username.lower()),
        )
        db.execute(
            "UPDATE scrape_log SET finished_at = ?, new_tweets = ?, status = 'done' WHERE id = ?",
            (now, new_count, log_id),
        )
        db.commit()

        result = {"username": username, "scraped": len(tweets), "new": new_count, "total": total}
        await send_telegram(
            f"🐦 <b>Scrape done:</b> @{username}\n"
            f"Scraped: {len(tweets)} | New: {new_count} | Total: {total}"
        )
        return result

    except Exception as e:
        db.execute(
            "UPDATE scrape_log SET finished_at = ?, status = 'error', error = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), str(e), log_id),
        )
        db.commit()
        await send_telegram(f"❌ <b>Scrape error:</b> @{username}\n{str(e)[:200]}")
        raise
    finally:
        db.close()


async def scrape_all_targets():
    """Scheduled job: scrape all active targets."""
    db = get_db()
    targets = db.execute("SELECT username FROM targets WHERE active = 1").fetchall()
    db.close()

    print(f"[CRON] Scraping {len(targets)} targets...")
    for row in targets:
        try:
            result = await scrape_user(row["username"])
            print(f"[CRON] @{row['username']}: {result['new']} new tweets")
        except Exception as e:
            print(f"[CRON] @{row['username']} error: {e}")
        await asyncio.sleep(5)  # rate limit buffer


# ── App Lifecycle ───────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if SCRAPE_INTERVAL > 0:
        scheduler.add_job(scrape_all_targets, "interval", hours=SCRAPE_INTERVAL, id="auto_scrape")
        scheduler.start()
        print(f"[*] Auto-scrape every {SCRAPE_INTERVAL}h")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Twitter Scraper", lifespan=lifespan)


# ── API Routes ──────────────────────────────────────────────────────────────
@app.post("/api/targets")
async def add_target(username: str = Query(...)):
    """Add a user to scrape list."""
    username = username.lower().lstrip("@")
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO targets (username, active) VALUES (?, 1)", (username,)
    )
    db.commit()
    db.close()
    return {"status": "added", "username": username}


@app.delete("/api/targets/{username}")
async def remove_target(username: str):
    db = get_db()
    db.execute("UPDATE targets SET active = 0 WHERE username = ?", (username.lower(),))
    db.commit()
    db.close()
    return {"status": "deactivated", "username": username}


@app.get("/api/targets")
async def list_targets():
    db = get_db()
    rows = db.execute("SELECT * FROM targets WHERE active = 1 ORDER BY username").fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.post("/api/scrape/{username}")
async def trigger_scrape(username: str, limit: int = Query(default=0)):
    """Trigger immediate scrape for a user."""
    username = username.lower().lstrip("@")
    # Auto-add as target
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO targets (username, active) VALUES (?, 1)", (username,)
    )
    db.commit()
    db.close()

    try:
        result = await scrape_user(username, limit=limit if limit > 0 else None)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scrape-all")
async def trigger_scrape_all():
    """Trigger scrape for all active targets."""
    asyncio.create_task(scrape_all_targets())
    return {"status": "started"}


@app.get("/api/tweets/{username}")
async def get_tweets(username: str, limit: int = Query(default=100), offset: int = Query(default=0)):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM tweets WHERE username = ? ORDER BY date DESC LIMIT ? OFFSET ?",
        (username.lower(), limit, offset),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.get("/api/download/{username}")
async def download_tweets(username: str, fmt: str = Query(default="txt")):
    """Download all tweets as .txt or .json file."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM tweets WHERE username = ? ORDER BY date DESC", (username.lower(),)
    ).fetchall()
    db.close()

    if not rows:
        raise HTTPException(404, "No tweets found for this user")

    if fmt == "json":
        content = "\n".join(json.dumps(dict(r), ensure_ascii=False) for r in rows)
        filename = f"@{username}_tweets.jsonl"
        return PlainTextResponse(content, media_type="application/jsonl",
                                 headers={"Content-Disposition": f"attachment; filename={filename}"})
    else:
        lines = [f"{'='*60}", f"  Tweets by @{username}", f"  Exported: {datetime.now().isoformat()}", f"  Total: {len(rows)}", f"{'='*60}", ""]
        for r in rows:
            lines.append(f"[{r['date']}]")
            lines.append(r["text"])
            lines.append(f"♥ {r['likes']}  ↻ {r['retweets']}  💬 {r['replies']}  |  {r['url']}")
            lines.append(f"{'─'*40}")
            lines.append("")
        content = "\n".join(lines)
        filename = f"@{username}_tweets.txt"
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8",
                                 headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.get("/api/log")
async def scrape_log(limit: int = Query(default=20)):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM scrape_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.get("/api/stats")
async def stats():
    db = get_db()
    targets = db.execute("SELECT COUNT(*) FROM targets WHERE active = 1").fetchone()[0]
    tweets = db.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    last = db.execute("SELECT finished_at FROM scrape_log WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    return {
        "targets": targets,
        "total_tweets": tweets,
        "last_scrape": last[0] if last else None,
        "interval_hours": SCRAPE_INTERVAL,
    }


# ── Dashboard ───────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Twitter Scraper</title>
<style>
  :root { --bg: #0a0a0a; --card: #141414; --border: #222; --accent: #1d9bf0; --green: #00ba7c; --red: #f4212e; --text: #e7e9ea; --muted: #71767b; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .container { max-width: 900px; margin: 0 auto; padding: 24px 16px; }
  h1 { font-size: 24px; margin-bottom: 4px; }
  .subtitle { color: var(--muted); font-size: 14px; margin-bottom: 24px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
  .stat-value { font-size: 28px; font-weight: 700; color: var(--accent); }
  .stat-label { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 16px; }
  .card h2 { font-size: 16px; margin-bottom: 12px; }
  .input-row { display: flex; gap: 8px; margin-bottom: 16px; }
  input[type="text"], input[type="number"] { background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 10px 14px; border-radius: 8px; font-size: 14px; flex: 1; outline: none; }
  input:focus { border-color: var(--accent); }
  button { background: var(--accent); color: #fff; border: none; padding: 10px 20px; border-radius: 8px; font-size: 14px; cursor: pointer; font-weight: 600; white-space: nowrap; }
  button:hover { opacity: 0.9; }
  button.secondary { background: var(--border); }
  button.danger { background: var(--red); }
  button.small { padding: 6px 12px; font-size: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; color: var(--muted); font-size: 12px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); }
  tr:hover td { background: rgba(255,255,255,0.02); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .badge-done { background: rgba(0,186,124,0.15); color: var(--green); }
  .badge-error { background: rgba(244,33,46,0.15); color: var(--red); }
  .badge-running { background: rgba(29,155,240,0.15); color: var(--accent); }
  .toast { position: fixed; bottom: 20px; right: 20px; background: var(--card); border: 1px solid var(--border); padding: 12px 20px; border-radius: 8px; display: none; z-index: 100; }
  .actions { display: flex; gap: 6px; }
  a { color: var(--accent); text-decoration: none; }
  .loading { opacity: 0.5; pointer-events: none; }
</style>
</head>
<body>
<div class="container">
  <h1>🐦 Twitter Scraper</h1>
  <p class="subtitle">Railway-deployed tweet collector</p>

  <div class="stats" id="stats">
    <div class="stat"><div class="stat-value" id="s-targets">-</div><div class="stat-label">Targets</div></div>
    <div class="stat"><div class="stat-value" id="s-tweets">-</div><div class="stat-label">Total Tweets</div></div>
    <div class="stat"><div class="stat-value" id="s-interval">-</div><div class="stat-label">Interval (h)</div></div>
    <div class="stat"><div class="stat-value" id="s-last">-</div><div class="stat-label">Last Scrape</div></div>
  </div>

  <div class="card">
    <h2>Add Target / Quick Scrape</h2>
    <div class="input-row">
      <input type="text" id="username" placeholder="@username (e.g. elonmusk)">
      <input type="number" id="limit" placeholder="Limit (0=all)" value="0" style="max-width:120px">
      <button onclick="scrapeUser()">Scrape Now</button>
      <button class="secondary" onclick="addTarget()">Add Only</button>
    </div>
  </div>

  <div class="card">
    <h2>Targets</h2>
    <div style="display:flex;justify-content:flex-end;margin-bottom:12px">
      <button class="small secondary" onclick="scrapeAll()">Scrape All Now</button>
    </div>
    <table>
      <thead><tr><th>User</th><th>Tweets</th><th>Last Scraped</th><th>Actions</th></tr></thead>
      <tbody id="targets-table"><tr><td colspan="4" style="color:var(--muted)">Loading...</td></tr></tbody>
    </table>
  </div>

  <div class="card">
    <h2>Recent Activity</h2>
    <table>
      <thead><tr><th>User</th><th>Status</th><th>New</th><th>Time</th><th>Error</th></tr></thead>
      <tbody id="log-table"><tr><td colspan="5" style="color:var(--muted)">Loading...</td></tr></tbody>
    </table>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
function toast(msg, ms=3000) { const t=$('#toast'); t.textContent=msg; t.style.display='block'; setTimeout(()=>t.style.display='none', ms); }
function ago(iso) { if(!iso) return '-'; const d=new Date(iso), m=Math.floor((Date.now()-d)/60000); return m<60?m+'m ago':m<1440?Math.floor(m/60)+'h ago':Math.floor(m/1440)+'d ago'; }
function fmt(n) { return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'K':String(n); }

async function api(method, path, body) {
  const r = await fetch('/api/'+path, {method, headers:{'Content-Type':'application/json'}, body:body?JSON.stringify(body):undefined});
  if(!r.ok) throw new Error((await r.json()).detail || r.statusText);
  return r.json();
}

async function loadStats() {
  const s = await api('GET','stats');
  $('#s-targets').textContent = s.targets;
  $('#s-tweets').textContent = fmt(s.total_tweets);
  $('#s-interval').textContent = s.interval_hours;
  $('#s-last').textContent = ago(s.last_scrape);
}

async function loadTargets() {
  const t = await api('GET','targets');
  const tb = $('#targets-table');
  if(!t.length) { tb.innerHTML='<tr><td colspan="4" style="color:var(--muted)">No targets yet</td></tr>'; return; }
  tb.innerHTML = t.map(r => `<tr>
    <td><strong>@${r.username}</strong></td>
    <td>${fmt(r.tweet_count)}</td>
    <td>${ago(r.last_scraped)}</td>
    <td class="actions">
      <button class="small" onclick="scrapeOne('${r.username}')">Scrape</button>
      <a href="/api/download/${r.username}?fmt=txt" class="small">📥 TXT</a>
      <a href="/api/download/${r.username}?fmt=json" class="small" style="margin-left:4px">JSON</a>
      <button class="small danger" onclick="removeTarget('${r.username}')">✕</button>
    </td>
  </tr>`).join('');
}

async function loadLog() {
  const l = await api('GET','log?limit=10');
  const tb = $('#log-table');
  if(!l.length) { tb.innerHTML='<tr><td colspan="5" style="color:var(--muted)">No activity yet</td></tr>'; return; }
  tb.innerHTML = l.map(r => `<tr>
    <td>@${r.username}</td>
    <td><span class="badge badge-${r.status}">${r.status}</span></td>
    <td>${r.new_tweets ?? '-'}</td>
    <td>${ago(r.finished_at || r.started_at)}</td>
    <td style="color:var(--red);font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis">${r.error||''}</td>
  </tr>`).join('');
}

async function refresh() { await Promise.all([loadStats(), loadTargets(), loadLog()]); }

async function addTarget() {
  const u = $('#username').value.trim().replace('@','');
  if(!u) return;
  await api('POST','targets?username='+u);
  toast('Added @'+u);
  $('#username').value='';
  refresh();
}

async function scrapeUser() {
  const u = $('#username').value.trim().replace('@','');
  const l = parseInt($('#limit').value)||0;
  if(!u) return;
  toast('Scraping @'+u+'...',10000);
  try {
    const r = await api('POST',`scrape/${u}?limit=${l}`);
    toast(`Done! @${u}: ${r.new} new, ${r.total} total`);
  } catch(e) { toast('Error: '+e.message, 5000); }
  refresh();
}

async function scrapeOne(u) {
  toast('Scraping @'+u+'...',10000);
  try {
    const r = await api('POST','scrape/'+u);
    toast(`Done! @${u}: ${r.new} new, ${r.total} total`);
  } catch(e) { toast('Error: '+e.message,5000); }
  refresh();
}

async function scrapeAll() {
  await api('POST','scrape-all');
  toast('Scraping all targets in background...');
}

async function removeTarget(u) {
  await api('DELETE','targets/'+u);
  toast('Removed @'+u);
  refresh();
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
