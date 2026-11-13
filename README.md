# 📡 Personal News & Trends Digest

Automated personal news & trends aggregator powered by **Python + GitHub Actions + GitHub Pages + Telegram** — with **no LLM required**.

It fetches updates three times a day (**08:00, 15:00, and 23:00 UTC+04:00**), filters and ranks them using keyword matching and popularity/velocity signals, deduplicates across runs, archives Markdown digests and a searchable static web archive in this GitHub repository, and delivers a balanced digest to your Telegram chat.

---

## Covered Subjects & Sources

**Subjects (6 Categories):**
1. **💻 Trending Tech News** (`trending_tech`)
2. **🌍 Trending Politics & World Affairs** (`trending_politics`)
3. **📈 Economics & Markets** (`economics`)
4. **🛠️ New Tools & Open Source** (`new_tools`)
5. **🤖 AI Agents & Agentic Workflows** (`ai_agents`)
6. **🧠 LLM Models & AI Research** (`llm_models`)

**Sources:**
- **Hacker News**: Algolia HN API (front page, recent stories, and targeted AI agent / MCP / Show HN queries) with Firebase HN API fallback.
- **GitHub**: Daily [GitHub Trending](https://github.com/trending?since=daily) scraper + GitHub Search API for fast-rising new repositories from the last 24 hours.
- **Free RSS / Atom Feeds**:
  - *World & Politics*: BBC World, The Guardian World, Al Jazeera, NYT World
  - *Economics & Markets*: BBC Business, The Guardian Business, CNBC Economy, NYT Economy
  - *Tech & Startups*: TechCrunch, Ars Technica, The Verge
  - *AI & Engineering*: Hugging Face Blog, Simon Willison's Weblog, OpenAI News, TechCrunch AI

All keywords, weights, limits, and feeds can be customized in [`config/config.yaml`](config/config.yaml).

---

## Setup Guide

### 1. Create a Telegram Bot & Get Your Chat ID

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Run `/newbot`, choose a name and username, and copy the **Bot Token** (`123456:ABC...`).
3. Open a chat with your new bot and send `/start` (or add the bot to a private channel/group and send a message there).
4. Discover your `TELEGRAM_CHAT_ID` locally:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   TELEGRAM_BOT_TOKEN="your-bot-token" .venv/bin/python -m src.cli get-chat-id
   ```

### 2. Test Locally (Dry Run)

```bash
.venv/bin/python -m src.cli run --dry-run --no-dedup
```

To test sending a real Telegram message from your machine:

```bash
TELEGRAM_BOT_TOKEN="your-bot-token" \
TELEGRAM_CHAT_ID="your-chat-id" \
.venv/bin/python -m src.cli run --no-dedup
```

### 3. Push to GitHub & Configure Secrets

1. Create a new repository on GitHub (e.g., `news-update`) and push:
   ```bash
   git add .
   git commit -m "Initial news digest engine"
   git remote add origin https://github.com/<your-username>/news-update.git
   git push -u origin main
   ```
2. In your GitHub repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN`: Your BotFather token
   - `TELEGRAM_CHAT_ID`: Your numeric Telegram chat ID

### 4. Enable GitHub Pages (Free Web Archive)

1. Go to **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **Deploy from a branch**.
3. Select branch **`main`** and folder **`/docs`**, then click **Save**.
4. Your searchable archive will be live at `https://<your-username>.github.io/<repo-name>/` (and linked at the bottom of every Telegram digest automatically).

> **Note:** GitHub Pages only hosts public repositories on the Free plan. If you want the repository to stay private, the Markdown files in `digests/` and Telegram delivery still work identically—you just won't use GitHub Pages unless on a paid plan or public repo.

### 5. Schedule & Exact-Minute Timing (Optional `cron-job.org`)

The workflow [`.github/workflows/digest.yml`](.github/workflows/digest.yml) runs automatically via GitHub Actions cron at:
- `0 4 * * *` → **08:00 UTC+04:00**
- `0 11 * * *` → **15:00 UTC+04:00**
- `0 19 * * *` → **23:00 UTC+04:00**

GitHub Actions `schedule` triggers can occasionally experience 5–20 minute queue delays during peak UTC hours. If you want exact-to-the-minute delivery:
1. Create a fine-grained GitHub Personal Access Token (PAT) with **Actions: Read and write** permission for this repo.
2. Create a free account on [cron-job.org](https://cron-job.org) and create 3 jobs at `08:00`, `15:00`, and `23:00` (`Asia/Dubai` or `UTC+4`) sending a `POST` request to:
   ```text
   https://api.github.com/repos/<your-username>/<repo-name>/actions/workflows/digest.yml/dispatches
   ```
   - Headers:
     - `Authorization: Bearer <YOUR_GITHUB_PAT>`
     - `Accept: application/vnd.github+json`
     - `User-Agent: cron-job-trigger`
   - Body:
     ```json
     {"ref": "main"}
     ```

### 6. On-Demand Telegram Bot Commands (Free Cloudflare Worker)

To request topic-specific briefs on-demand in Telegram (`/economy`, `/politics`, `/brief politics economy`, `/tech`, `/tools`, `/agents`, `/llm`, `/topics`):

1. Create a free [Cloudflare Workers](https://dash.cloudflare.com) Worker and deploy [`cloudflare-worker/worker.js`](cloudflare-worker/worker.js) (either paste it in the Cloudflare Dashboard or run `npx wrangler deploy` from `cloudflare-worker/`).
2. Add these **Secrets / Variables** in your Worker's **Settings → Variables and Secrets**:
   - `TELEGRAM_BOT_TOKEN`: Your BotFather token
   - `TELEGRAM_WEBHOOK_SECRET`: Any random secret string (e.g. `my-secret-token-123`)
   - `ALLOWED_CHAT_ID`: Your numeric `TELEGRAM_CHAT_ID`
   - `GITHUB_PAT`: Fine-grained GitHub PAT with **Actions: Read and write** on this repo
   - `GITHUB_REPO`: `<your-username>/news-update`
3. Register the Telegram webhook (replace `<BOT_TOKEN>`, `<WORKER_URL>`, `<WEBHOOK_SECRET>`):
   ```bash
   curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url":"<WORKER_URL>","secret_token":"<WEBHOOK_SECRET>"}'
   ```
4. *(Optional)* Register the `/` command menu with Telegram:
   ```bash
   curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setMyCommands" \
     -H "Content-Type: application/json" \
     -d '{"commands":[{"command":"brief","description":"Latest brief (or: /brief politics economy)"},{"command":"tech","description":"Trending Tech News"},{"command":"politics","description":"Trending Politics & World Affairs"},{"command":"economy","description":"Economics & Markets"},{"command":"tools","description":"New Tools & Open Source"},{"command":"agents","description":"AI Agents & Agentic Workflows"},{"command":"llm","description":"LLM Models & AI Research"},{"command":"topics","description":"List available commands"}]}'
   ```

---

## Project Structure

- `config/config.yaml`: Categories, keywords, RSS URLs, and selection limits
- `cloudflare-worker/`: Free 24/7 Telegram webhook bridge to GitHub Actions
- `src/fetchers/`: Hacker News, GitHub Trending/Search, and RSS feed fetchers
- `src/classifier.py`: Rule-based keyword matcher and popularity/velocity ranker
- `src/dedup.py`: URL normalization and title fingerprint deduplication
- `src/formatters/`: Markdown archival, Telegram HTML sender, and static GitHub Pages builder
- `digests/`: Saved Markdown editions (`digests/YYYY/MM/YYYY-MM-DD-HHMM.md`)
- `docs/`: Auto-generated GitHub Pages static site
- `data/`: `seen_items.json` (dedup history) and `digests_index.json`
