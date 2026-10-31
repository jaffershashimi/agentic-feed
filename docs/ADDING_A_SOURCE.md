# Adding a data source (checklist)

For anyone adding an API to the system. Decide first where the data may live: this repository is public.

## 1. Classify it (record the answer in [DATA_SOURCES.md](DATA_SOURCES.md))

| Class | Meaning | Where it runs |
|---|---|---|
| use | Terms allow publishing what we derive | `feed_pipeline/` here, published in `feed/` |
| internal | May feed a published derived value; the raw value is never published | `feed_pipeline/` here |
| consumer-side | Licensed or personal (most per-ticker prices, option chains, anything with a personal key) | Only in private desk runs (Claude connectors) or the future mini PC; never in this repository |
| skip | Unofficial or terms forbid it | Nowhere |

Most commercial market-data APIs (per-ticker quotes, chains) are **consumer-side**. Public government data (FRED, Treasury, SEC EDGAR, CFTC) is usually **use**.

## 2. If it belongs in this repo

1. Add a fetcher in `feed_pipeline/sources.py` following `fetch_fred`: use `fetch_text` (User-Agent, retries), return a `FetchResult`, and never raise on a network error; report it instead.
2. Keys go only in GitHub Actions secrets (Settings, Secrets and variables, Actions) and are read from the environment in `.github/workflows/publish.yml`; `_scrub` removes a key from any error text.
3. Add the value to `feed/desk-feed.json` only if it fits the 6,000-byte budget; otherwise publish a separate file under `feed/` and list it in [FEED_SCHEMA.md](FEED_SCHEMA.md).
4. Add a unit test with a saved sample response (no network in tests).
5. Run `python3 -m unittest discover -s tests -t .` and `python3 scripts/privacy_check.py`, then open a PR.

## 3. If it is consumer-side

Add it to the private hub instead: note the API, limits, key location (never the key) and which paper run should use it in `Agentic-Lab/system/07-engineering/DATA_PLAN.md` style notes, and Claude adds it to the run prompts. On the mini PC it becomes an adapter in the private service (see the hub blueprint).

## 4. After it is live

Update [system/OPERATING_STATE.md](../system/OPERATING_STATE.md) if a run starts depending on it, and recheck its terms once a year.
