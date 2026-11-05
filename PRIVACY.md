# Privacy Policy

This app runs entirely on your own computer and listens on `127.0.0.1` only. There is no account, no sign-up, no analytics, no cookies and no server operated by the project. This page describes what the app stores and which third parties it contacts.

## Data we collect and store (locally)

| Data | Where | Retention |
|---|---|---|
| Requests you type and the reports generated | `tools/output/history/` | Last 100 runs, then oldest deleted |
| Cached market data | `tools/output/cache/` | Short-lived cache; cleared on request |
| UI settings | Browser `localStorage` (`ta_settings`, `ta_notice`) | Until you clear browser data |
| Session log, closed trades, positions | `memory/sessions.md`, `memory/trades.md`, `memory/positions.json` | Until you delete them; gitignored |

None of this is uploaded by the app. The repository is public: never commit these files (they are gitignored).

## Third parties that receive data

Market data is fetched from public, keyless endpoints. Each sees your IP address and the symbols requested:

- OKX, Swissquote, CoinGecko, Deribit, Yahoo Finance (via `yfinance`), FRED.
- **Anthropic (Claude CLI)**, only for "Full agent" runs: your request text and the analysis context are sent to Anthropic by `claude -p`, under your own Claude account and Anthropic's privacy policy.

We do not sell data and do not use advertising or tracking services.

## Deleting your data

- In the app: **History → Delete all history** (also removes the market-data cache), or delete a single run.
- Manually: delete `tools/output/history/`, `tools/output/cache/` and the `memory/` private files.

## Protecting yourself

- Never paste API keys, passwords or seed phrases into the app. They would be saved in history.
- Keep the server bound to localhost; do not expose it to a network.
- Reports are analysis only, not financial advice. You make and execute every trade.
