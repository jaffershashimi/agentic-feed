# Vendored: day-trader-skill (MIT)

Source: https://github.com/jakeschincariol/day-trader-skill, commit 3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd (2026-10-20), "The Trader skill v2: 11 day-trading skills".
Author: Jake Schincariol. License: MIT (see LICENSE in this folder). Copied unchanged: the eleven `trader-*` skill folders and their offline tests with fixtures.
Why it is here: Agentic Lab uses its methods (opening-range breakout, exit planning, bar-bootstrap replay with a break-even-aware verdict, risk gate, price alerts, filings readers). See docs/KNOWLEDGE_CORE.md for what is used and what is not.
Rules for this folder: do not edit the vendored files (an update means re-vendoring a new pinned commit); our own code lives in consumer/day_trade_kit.py and imports from here. The vendored tools can place no order (their broker step only prints a ticket), and nothing here is called by a schedule except through our guarded tools.
