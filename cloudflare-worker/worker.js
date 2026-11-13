/**
 * Cloudflare Worker: Telegram Bot Webhook -> GitHub Actions workflow_dispatch bridge.
 *
 * Required Worker Secrets / Environment Variables:
 *   - TELEGRAM_BOT_TOKEN: Bot token from @BotFather
 *   - TELEGRAM_WEBHOOK_SECRET: Random secret string passed to Telegram setWebhook
 *   - ALLOWED_CHAT_ID: Your personal Telegram chat ID (e.g. "123456789")
 *   - GITHUB_PAT: Fine-grained GitHub PAT with Actions: Read & write on your repo
 *   - GITHUB_REPO: "owner/repo" (e.g. "Dutch-panda/news-update")
 *   - GITHUB_WORKFLOW_ID: Workflow filename (default: "digest.yml")
 *   - GITHUB_REF: Branch name (default: "main")
 */

const TOPIC_SHORTCUTS = {
  "/tech": "trending_tech",
  "/politics": "trending_politics",
  "/world": "trending_politics",
  "/economy": "economics",
  "/economics": "economics",
  "/markets": "economics",
  "/tools": "new_tools",
  "/agents": "ai_agents",
  "/llm": "llm_models",
  "/ai": "llm_models",
};

const HELP_TEXT = [
  "<b>📡 News &amp; Trends On-Demand Bot</b>",
  "",
  "<b>Topic Commands:</b>",
  "• /brief — Latest brief across all topics",
  "• /brief politics economy — Combine any topics",
  "• /tech — 💻 Trending Tech News",
  "• /politics — 🌍 Trending Politics &amp; World Affairs",
  "• /economy — 📈 Economics &amp; Markets",
  "• /tools — 🛠️ New Tools &amp; Open Source",
  "• /agents — 🤖 AI Agents &amp; Agentic Workflows",
  "• /llm — 🧠 LLM Models &amp; AI Research",
  "• /topics — Show this command list",
].join("\n");

async function sendTelegramReply(token, chatId, text) {
  await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
}

async function triggerDigestWorkflow(env, chatId, topics) {
  const repo = env.GITHUB_REPO;
  const workflow = env.GITHUB_WORKFLOW_ID || "digest.yml";
  const ref = env.GITHUB_REF || "main";

  const resp = await fetch(
    `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_PAT}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "news-update-cf-worker",
      },
      body: JSON.stringify({
        ref,
        inputs: {
          topics: topics || "all",
          chat_id: String(chatId),
          on_demand: "true",
        },
      }),
    }
  );

  if (!resp.ok) {
    const errBody = await resp.text();
    throw new Error(`GitHub dispatch failed (${resp.status}): ${errBody}`);
  }
}

export default {
  async fetch(request, env) {
    if (request.method === "GET") {
      return new Response("OK", { status: 200 });
    }
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    // Verify Telegram webhook secret if configured
    if (env.TELEGRAM_WEBHOOK_SECRET) {
      const provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
      if (provided !== env.TELEGRAM_WEBHOOK_SECRET) {
        return new Response("Unauthorized", { status: 401 });
      }
    }

    const update = await request.json();
    const message = update.message || update.edited_message;
    if (!message || !message.text || !message.chat) {
      return new Response("OK", { status: 200 });
    }

    const chatId = String(message.chat.id);
    if (env.ALLOWED_CHAT_ID && chatId !== String(env.ALLOWED_CHAT_ID).trim()) {
      await sendTelegramReply(env.TELEGRAM_BOT_TOKEN, chatId, "⛔ Unauthorized chat.");
      return new Response("OK", { status: 200 });
    }

    const rawText = message.text.trim();
    const parts = rawText.split(/\s+/);
    const cmd = parts[0].split("@")[0].toLowerCase();

    if (cmd === "/start" || cmd === "/help" || cmd === "/topics") {
      await sendTelegramReply(env.TELEGRAM_BOT_TOKEN, chatId, HELP_TEXT);
      return new Response("OK", { status: 200 });
    }

    let requestedTopics = "";
    if (cmd === "/brief") {
      requestedTopics = parts.slice(1).join(",") || "all";
    } else if (TOPIC_SHORTCUTS[cmd]) {
      requestedTopics = TOPIC_SHORTCUTS[cmd];
    } else {
      await sendTelegramReply(
        env.TELEGRAM_BOT_TOKEN,
        chatId,
        "Unknown command. Send /topics to see available commands."
      );
      return new Response("OK", { status: 200 });
    }

    await sendTelegramReply(
      env.TELEGRAM_BOT_TOKEN,
      chatId,
      `⏳ Fetching latest 24h updates for: <code>${requestedTopics}</code> (~30s)…`
    );

    try {
      await triggerDigestWorkflow(env, chatId, requestedTopics);
    } catch (err) {
      await sendTelegramReply(
        env.TELEGRAM_BOT_TOKEN,
        chatId,
        `❌ Failed to trigger brief: ${String(err.message || err)}`
      );
    }

    return new Response("OK", { status: 200 });
  },
};
