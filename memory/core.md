# Core

Stable facts about this agent and setup. Changes only via MEMORY_UPDATE (see memory-protocol.md).

- Agent is analysis-only; the user executes all trades. (added: 2026-10-04)
- Data sources: yfinance, plus HTTP APIs via `requests`. (added: 2026-10-04)
- Analysis outputs are saved to `tools/output/`. (added: 2026-10-04)
- All timestamps stored in UTC. (added: 2026-10-04)
