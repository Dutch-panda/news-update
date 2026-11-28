# 📡 Personal News & Trends Digest

Automated personal news & trends aggregator powered by **Python + GitHub Actions + GitHub Pages + Telegram** — with **no LLM required**.

It delivers a focused **Daily Morning Brief** (up to 16 items over a 24h window) while letting you request any of the broader topics **on-demand** via Telegram bot commands. It archives Markdown digests and a searchable static web archive in GitHub and delivers formatted messages to Telegram.

---

## Covered Subjects & Sources

**Daily Morning Brief (`on_demand_only: false`, 24h Window, max 16 items):**
1. **📊 Data Science, Statistics & Analytics (Guides & Case Studies)** (`ds_edu`, max 3)
2. **🔥 Top 10 Hacker News** (`hackernews_top`, max 10)
3. **⚡ Top 3 TechCrunch** (`techcrunch_top`, max 3)

**On-Demand Only Topics (via Telegram Bot, 24h Window):**
4. **💻 Trending Tech News** (`trending_tech`, max 15)
5. **📊 Data Science & Business Analytics** (`data_science`, max 15)
6. **🛠️ Open-Source Projects, Tools & Harnesses** (`new_tools`, max 15)
7. **🤖 Agentic Workflows, MCPs & Skills** (`ai_agents`, max 15)
8. **🧠 LLM Models & Capabilities** (`llm_models`, max 15)
9. **📈 Business, Economics & Case Studies** (`economics`, max 10)
10. **🌍 Trending Politics & World Affairs** (`trending_politics`, max 10)

**Sources (All Free, Non-Paywalled):**
- **Hacker News**: Algolia HN popularity-ranked `/search` API with Firebase HN API fallback.
- **GitHub** *(on-demand tech/AI topics)*: Daily [GitHub Trending](https://github.com/trending?since=daily) across All, Python, TypeScript, and Rust + GitHub Search API for breakout new repos (`>= 150` stars in last 5 days, spam-filtered).
- **Free RSS / Atom Feeds**:
  - *DS, Stats & Analytics (Guides & Case Studies)*: Towards Data Science, KDnuggets, DuckDB Blog, FlowingData, Machine Learning Mastery, Analytics Vidhya *(plus Reddit `r/datascience` & `r/BusinessIntelligence` and ArXiv `cs.LG`/`stat.ML` for on-demand `/data`)*
  - *TechCrunch*: TechCrunch, TechCrunch AI
  - *AI, LLMs & Agentic (on-demand)*: Hugging Face Blog, Simon Willison's Weblog, OpenAI News, Reddit `r/LocalLLaMA`, ArXiv `cs.AI`
  - *Other Tech (on-demand)*: Ars Technica, The Verge
  - *Business, Economics & Case Studies (on-demand)*: MIT Sloan Management Review, Pew Research, Calculated Risk, BBC Business, The Guardian Business, CNBC Economy
  - *Politics & World Affairs (on-demand)*: BBC World, The Guardian World, Al Jazeera, NPR World, DW News

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

1. **Create the Worker (Account-level, NOT inside a Domain):**
   - Go to [dash.cloudflare.com](https://dash.cloudflare.com) and stay on your **Account Home** (do **not** click into a domain/website).
   - In the left sidebar, click **Compute (Workers) → Workers & Pages** (or **Workers & Pages**).
   - Click **Create** → **Create Worker** (or **Start with Hello World!**), name it `news-update-telegram-bot`, and click **Deploy**.
   - Click **Edit code**, replace all code in `worker.js` with the contents of [`cloudflare-worker/worker.js`](cloudflare-worker/worker.js), and click **Deploy** (top right).
   - Copy your free Worker URL (e.g. `https://news-update-telegram-bot.<your-subdomain>.workers.dev`). You do **not** need a custom domain.
   *(Alternative via CLI from `cloudflare-worker/`: `npx wrangler login && npx wrangler deploy`)*
2. **Add Secrets / Variables** in your Worker's **Settings → Variables and Secrets → Add**:
   - `TELEGRAM_BOT_TOKEN` *(Secret)*: Your BotFather token
   - `TELEGRAM_WEBHOOK_SECRET` *(Secret)*: Any random secret string (e.g. `my-secret-token-123`)
   - `ALLOWED_CHAT_ID` *(Text)*: Your numeric `TELEGRAM_CHAT_ID`
   - `GITHUB_PAT` *(Secret)*: Fine-grained GitHub PAT with **Actions: Read and write** on this repo
   - `GITHUB_REPO` *(Text)*: `<your-username>/news-update`
   - `GITHUB_WORKFLOW_ID` *(Text)*: `digest.yml`
   - `GITHUB_REF` *(Text)*: `main`
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
     -d '{"commands":[{"command":"brief","description":"Morning Brief (Guides + Top 10 HN + Top 3 TC)"},{"command":"guides","description":"DS, Statistics & Analytics Guides (max 3)"},{"command":"hn","description":"Top 10 Hacker News"},{"command":"tc","description":"Top 3 TechCrunch"},{"command":"tech","description":"Trending Tech News"},{"command":"data","description":"Data Science & Business Analytics"},{"command":"tools","description":"Open-Source Projects, Tools & Harnesses"},{"command":"agents","description":"Agentic Workflows, MCPs & Skills"},{"command":"llm","description":"LLM Models & Capabilities"},{"command":"economy","description":"Business, Economics & Case Studies"},{"command":"politics","description":"Trending Politics & World Affairs"},{"command":"all","description":"All sections & topics (24h window)"},{"command":"topics","description":"List available commands"}]}'
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
