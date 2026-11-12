# Slack query integration (not built yet)

Plan: a Slack Bolt for Python app in Socket Mode (no public URL needed) that
runs on the lab Mac next to Asset Manager.

- Slash command `/lab <query>` and `@AssetBot <query>` mentions.
- Each query goes to `app.core.services.search.search()` (same syntax as the
  app's search bar, e.g. `HeLa`, `cat:plasmid state:out`, `vials<2`) and
  `app.core.services.inventory.report_text()` for "재고 현황".
- The project file is SQLite in WAL mode, so the bot can open it read-only
  while the app is running.

Scopes: bot token `app_mentions:read`, `chat:write`, `commands`
(+ `im:history` for DMs); app-level token `connections:write`.
