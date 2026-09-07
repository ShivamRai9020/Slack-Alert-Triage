# Intelligent Alert Triage Bot — Phase 1 MVP


## Fix (2026-09-07c): channel names + simpler dashboard

- Channel columns show Slack **#channel-name** (via `conversations.info`, cached), not raw `C…` IDs when the bot token can resolve them.
- Dashboard simplified: Ingested / Notified / Jira / Suppressed; two charts; leaner alert table; Advanced config stays collapsed.
- Needs Slack scope for name lookup: `channels:read` (and `groups:read` for private channels).

---

## Fix (2026-09-07b): priority notify + simpler dashboard

**Notify policy**
- **P0:** notify each firing (default 2‑minute debounce via `P0_DEBOUNCE_MINUTES`)
- **Other priorities:** notify only after **5+** firings in 60 minutes (`SPIKE_THRESHOLD` / `SPIKE_WINDOW_MINUTES`), then wait `NOTIFICATION_COOLDOWN_MINUTES` before notifying again on the same spike

**Dashboard:** Routing Config and Channel → Vertical Mapping are collapsed under **Advanced config (optional)**. Day-to-day use relies on alert text (techteam / priority) and source-channel notify fallback. APIs `/config/verticals` and `/config/channels` remain for power users.

---

## Fix (2026-09-07): notifications + dashboard auto fields

**Root cause of missing channel notifications:** the Slack listener dropped
every event with `bot_id` or any `subtype`. Alertmanager / Slack apps post
as bots (often `subtype=bot_message`), so real alerts never entered the
pipeline.

**What changed:**
1. Ingest bot-posted alerts (still skips our own triage posts and non-message subtypes). Also folds attachment/block text when top-level `text` is empty.
2. Dashboard shows **Techteam** (from alert `techteam` → vertical), **Priority**, and **Channel** from the alert itself — no channel→vertical mapping required for display.
3. If Routing Config has no notify channel, the bot **falls back to notifying the source channel** (with a warning log) instead of silently doing nothing. Prefer still adding a Routing Config row for a dedicated triage channel.
4. `POST /internal/events` accepts optional `channel_name` for nicer dashboard labels.

Still recommended for production: invite the bot to source + notify channels, use Slack **channel IDs**, set `SLACK_BOT_TOKEN` / `chat:write`, and add `vertical_config` rows for where triage posts should go.

---

Analyzes incoming Slack alerts, correlates them against history, applies a
transparent rule-based classifier, and posts an actionable recommendation
to the responsible team's channel. **It never mutes/acknowledges/modifies
alerts itself** — it only recommends; a human always takes the action.

## What's built in this MVP

- Slack ingestion (Socket Mode for local dev — no public URL needed)
- Per-channel format-profile parser (regex-based, extend in `app/parsing/profiles.py`)
- Fingerprinting engine that identifies "the same alert type" across occurrences
  (see the design note at the top of `app/fingerprinting/fingerprint.py` — this
  is the piece most worth reviewing carefully before you trust its output)
- Historical stats (1h/24h/7d/30d occurrence counts, avg interval)
- Deterministic rule-based triage (new / recurring-normal / potential-noise / spike)
- Config-driven vertical→channel routing (`vertical_config` table, editable via API)
- Template-based recommendation composer + Slack dispatcher with a cooldown
  (won't re-notify the same alert type more than once per
  `NOTIFICATION_COOLDOWN_MINUTES`)
- Mock alert generator so you can exercise the whole pipeline without
  production Slack access

## Not yet built (later phases, per the plan)

Dashboard UI, Jira integration, LLM-assisted composer, per-person on-call
schedule lookup, Kubernetes deployment, Alembic migrations (MVP uses
`create_all` — see note in `app/db/session.py`).

## Running locally

### Option A: no Docker (works on a locked-down laptop)

Everything here is plain Python -- Docker is a convenience, not a
requirement. This path uses a local virtualenv and SQLite (no database
server needed either), and picks up the dialect-aware fixes already in
`app/db/models.py` / `app/dashboard/queries.py`.

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
# If your network blocks binary wheel downloads for `psycopg` (the
# Postgres driver) and installation fails, comment out the
# `psycopg[binary]` line in requirements.txt and re-run -- it's only
# needed if you point DATABASE_URL at a real Postgres instance later,
# not for the default SQLite setup.

cp .env.example .env
# .env.example already defaults DATABASE_URL to sqlite:///./app.db --
# no database server to install or configure. Leave Slack/Jira/LLM
# blank for now (dry-run mode).

alembic upgrade head            # creates app.db with all tables
uvicorn app.main:app --reload   # starts on http://localhost:8000
```

In another terminal (same venv activated):
```bash
python mocks/mock_alert_generator.py --scenario all
pytest tests/ -v
```

Watch the `uvicorn` terminal for `[DRY-RUN] Would notify #...` lines, and
check `http://localhost:8000/dashboard` in a browser.

**Moving to real Slack/Jira later needs no code changes** — just fill in
the real values in `.env`; Socket Mode (used for the Slack listener)
works the same with or without Docker, since it's an outbound
connection your laptop initiates, not an inbound webhook needing a
public URL.

**If you eventually need Postgres** (e.g. once more than one person/
process needs concurrent write access — SQLite doesn't handle that well)
and can't install it locally either, a free-tier hosted Postgres
(Neon, Supabase, Railway all have one) works fine — just point
`DATABASE_URL` at its connection string and re-run `alembic upgrade head`.

### Option B: Docker

```bash
cp .env.example .env
# fill in SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_APP_TOKEN
# (App Token needs Socket Mode enabled + connections:write scope —
#  see "Slack App Setup" from our design discussion)

docker-compose up --build
```

The app starts on `http://localhost:8000`. Check `/health`.

### Running without a Slack app at all (fastest way to try the pipeline)

You don't need real Slack credentials to test the core logic — leave
`SLACK_BOT_TOKEN` blank and the app runs in dry-run mode (notifications are
printed to the console instead of posted to Slack):

```bash
docker-compose up --build
# in another terminal:
docker-compose exec app python mocks/mock_alert_generator.py --scenario all
```

Watch the `app` container logs — you'll see `[DRY-RUN] Would notify #...`
messages showing the composed recommendation for each scenario
(new alert, noisy recurring alert, and a frequency spike).

### Seeding vertical/team config

Before alerts can be routed anywhere, add at least one vertical config.
Routing resolves most-specific-first: `alert_name` override > `service`
default > `vertical` default. **These endpoints require the
`X-API-Key` header** (matching `INTERNAL_API_KEY` in `.env`) once you've
set one — omit the header entirely only if you've left
`INTERNAL_API_KEY` blank for local dev.

```bash
# Vertical-level default (applies to any service/alert in "payments")
curl -X POST http://localhost:8000/config/verticals \
  -H "X-API-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "vertical": "payments",
    "service": null,
    "alert_name": null,
    "alert_channel": "vertical-a-alerts",
    "dev_notify_channel": "payments-dev-oncall",
    "devops_notify_channel": "payments-devops-oncall"
  }'

# Service-level override (checkout-api alerts go to a different channel)
curl -X POST http://localhost:8000/config/verticals \
  -H "X-API-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "vertical": "payments",
    "service": "checkout-api",
    "alert_name": null,
    "alert_channel": "vertical-a-alerts",
    "devops_notify_channel": "checkout-devops-oncall"
  }'

# Alert-type-level override (DB alerts on checkout-api go to DBA on-call
# even though the service default above points elsewhere)
curl -X POST http://localhost:8000/config/verticals \
  -H "X-API-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "vertical": "payments",
    "service": "checkout-api",
    "alert_name": "Database connection failures",
    "alert_channel": "vertical-a-alerts",
    "devops_notify_channel": "dba-oncall"
  }'
```

## Running tests

The fingerprinting and triage engine are pure logic with no DB/Slack
dependency, so they're the fastest to test:

```bash
docker-compose exec app pytest tests/ -v
```

(Or locally with a venv: `pip install -r requirements.txt && pytest tests/ -v`
— note dependency installation requires network access.)

## Debug endpoints

- `GET /health`
- `GET /dashboard?days=7` — metrics dashboard (totals, by-vertical/severity, top recurring alerts, noise/investigation queues, 7-day trend, notification-channel split)
- `GET /alerts/{fingerprint_id}/stats`
- `GET /alerts/{fingerprint_id}/history`
- `GET /config/verticals`, `POST /config/verticals`
- `GET /incidents?status=open` — list tracked incidents
- `POST /incidents/{id}/resolve` — manually mark an incident resolved (ticket creation is automatic; closing is always a human action)
- `POST /internal/events` — manually feed a raw alert (used by the mock generator)

## Phase 3 additions

1. **Jira integration** (`app/jira/`) — for alert classifications serious
   enough to warrant tracking (`high_frequency_spike` always,
   `requires_investigation` only when severity is CRITICAL — see
   `should_open_incident` in `app/jira/service.py`, unit-tested in
   isolation from Jira/DB), the bot:
   - checks for an existing **open** incident for that alert fingerprint
   - if one exists, adds a Jira comment noting the recurrence instead of creating a duplicate ticket
   - if none exists, creates a new ticket labeled with the fingerprint hash, and opens an `Incident` row linking them
   - **Without Jira credentials configured, this runs in dry-run mode** — tickets are logged, not created (same pattern as the Slack dry-run mode from Phase 1). Fill in `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` in `.env` to go live.
   - Deliberately conservative: `potential_noise` and `recurring_normal` never open tickets — the whole point of the triage engine is to reduce alert fatigue, and auto-ticketing noisy alerts would just relocate the fatigue into Jira.
2. **Correlation** (`app/correlation/`) — when an incident-worthy alert
   fires, the bot looks for other *distinct* alert types that fired in
   the same vertical/service in the last `CORRELATION_WINDOW_MINUTES`
   (default 15) and lists them in the Slack notification as "possibly
   related." This is co-occurrence, not causation — a hint for the
   on-call to look at together, not a root-cause claim.
3. **Incident tracking** (`Incident` model + `/incidents` endpoints) —
   tracks fingerprint ↔ Jira ticket ↔ status. Ticket *creation* is
   automated (non-destructive); ticket *resolution* stays a manual
   `POST /incidents/{id}/resolve` call, consistent with the
   human-in-the-loop constraint on the rest of the system.

### Jira App setup

1. In Jira, go to your account settings → **Security** → **Create and manage API tokens** → create a token
2. Note your Jira Cloud base URL (`https://yourcompany.atlassian.net`), the email tied to the token, and the project key you want tickets created in (e.g. `OPS`)
3. Fill these into `.env` as `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`

### Trying Phase 3 with the mock generator

The `spike` scenario in `mocks/mock_alert_generator.py` fires 20 occurrences of a CRITICAL alert in quick succession — enough to trigger `high_frequency_spike` and, in dry-run mode, log a `[DRY-RUN] Would create Jira issue ...` line. Run it twice in a row and the second run should log an *update to the existing* dry-run ticket instead of creating a second one — that's the duplicate-ticket-avoidance logic working.

### Merge note: SQLite-compatibility fixes

The version you uploaded (based on the Phase 2 codebase) included several
real fixes for running against SQLite locally, which are now merged into
this Phase 3+4 tree:
- `app/db/models.py` -- a dialect-aware `JSON_TYPE` (JSONB on Postgres,
  plain JSON elsewhere) since `JSONB` is Postgres-only and previously
  broke `create_all()` on SQLite; primary keys switched from `BigInteger`
  to `Integer` with `autoincrement=True` so SQLite maps them to its
  native rowid-aliased autoincrementing key
- `app/dashboard/queries.py` -- `date_trunc` (Postgres-only) now falls
  back to SQLite's `strftime` for the frequency-trend query
- `app/dashboard/routes.py` -- `TemplateResponse` called with keyword
  args for compatibility with newer Starlette
- `app/notification/dispatcher.py` -- the cooldown check now strips
  tzinfo before comparing datetimes, since SQLite returns naive
  datetimes even for `TIMESTAMPTZ`-typed columns, which previously
  raised on comparison against an aware `datetime.now(timezone.utc)`
- `app/routing/router.py` -- `scalar_one_or_none()` swapped for
  `scalars().first()`

One thing to flag: the uploaded version also didn't include the Jira/
incident/correlation work from Phase 3, and the Slack duplicate-delivery
guard (`dedup_guard.py`) existed as a file but wasn't wired into
`slack_listener.py` anymore. Both are consistent with having forked from
a slightly earlier state rather than a deliberate removal, so both are
still active in this merged tree -- flag it if either was actually meant
to come out.

## Phase 4 additions

1. **LLM client** (`app/llm/client.py`) -- wraps a **local Ollama**
   instance behind `ENABLE_LLM_COMPOSER`. Runs entirely on your machine,
   no API key or account needed. Any failure (Ollama not running,
   timeout, unexpected response) returns `None` rather than raising, so
   an LLM outage can never block alert processing -- this is the
   enforcement point for "system stays reliable if the LLM is
   unavailable."
2. **Natural-language explanation** (`app/llm/explain.py`) -- a short,
   plain-English addendum appended to the Slack message as *"🤖 AI
   summary:"*, clearly separated from and never replacing the
   deterministic classification/rationale.
3. **Semantic duplicate detection** (`app/llm/pattern_detection.py`) --
   only runs on first-occurrence (`new_unknown`) alerts, and only asks
   the LLM to pick from a list of the service's existing tracked alert
   names -- never to invent a name. A response is trusted only if it's a
   **verbatim** match to one of the candidates sent (see
   `test_pattern_detection.py::test_non_verbatim_hallucinated_response_is_rejected`),
   which guards against the model hallucinating a plausible-sounding but
   wrong duplicate. This never auto-merges fingerprints -- it only
   surfaces a suggestion for a human to act on.

### Setting up Ollama (free, local, no account needed)

1. Install from https://ollama.com (macOS/Linux/Windows)
2. Pull the model: `ollama pull llama3.2`
3. Make sure it's running: `ollama serve` (often starts automatically
   after install -- check with `curl http://localhost:11434` first)
4. In `.env`: `ENABLE_LLM_COMPOSER=true`, `LLM_MODEL=llama3.2`
5. Restart `uvicorn` fully (env vars only load at process startup)

With `ENABLE_LLM_COMPOSER` left unset/false, the system behaves exactly
as before -- `LLMClient.enabled` is `False` and both `explain()` and
`suggest_duplicate()` short-circuit to `None` immediately, no Ollama
required at all. If Ollama isn't running when a call is attempted, the
client logs a clear "Could not connect to Ollama. Is it running?"
warning and falls back to the deterministic message with no crash --
worth testing deliberately (stop `ollama serve`, post a test alert,
confirm the Slack message still arrives without the AI summary line)
the same way the Jira and Slack fail-safes were verified earlier.

**Swapping back to a hosted model (e.g. Anthropic's API) later:** the
`LLMClient` interface (`.enabled`, `.complete(system, user, max_tokens)`)
is what `explain.py` and `pattern_detection.py` depend on -- any
implementation behind that same interface drops in without touching
those two files.

### Schema change note (again)

Phase 3 adds a new `incidents` table. Same caveat as Phase 2 — the MVP
uses `create_all()`, not Alembic, so an existing dev DB volume needs
`docker-compose down -v` (or a manual `CREATE TABLE incidents (...)`
matching `app/db/models.py`) to pick up the new table. This is now the
second schema change handled this way — genuinely worth migrating to
Alembic before Phase 4 rather than a third time.

1. **Alert-type-level routing** — `vertical_config` now has a nullable
   `alert_name` column. Resolution precedence is alert_name > service >
   vertical (see `app/routing/router.py`). If you already ran Phase 1
   against a real Postgres DB, this is a schema change — see "Schema
   change note" below.
2. **Duplicate-delivery guard** — `app/ingestion/dedup_guard.py` dedups
   on `(channel, slack_ts)` before an event reaches the pipeline, so a
   Slack Events API retry doesn't get counted as a second real
   occurrence. **This is process-local (in-memory)** — fine for a single
   app instance; if you scale to multiple replicas, swap it for a Redis
   `SETNX`-with-expiry before relying on it.
3. **Dashboard** — `GET /dashboard`, server-rendered (FastAPI + Jinja2,
   no separate frontend build). Answers "where are we spending the most
   SRE time" per your original ask: total alerts, by vertical/severity,
   top 10 most frequent alerts, the potential-noise and
   needs-investigation queues, a 7-day frequency trend, and a
   notifications-per-channel breakdown (a proxy for Dev vs. DevOps load
   until channels are explicitly tagged with a role — see note in
   `app/dashboard/queries.py`).

### Schema change note

The MVP uses `Base.metadata.create_all()` rather than Alembic (documented
as a known shortcut in `app/db/session.py`). If you have an existing
Postgres volume from Phase 1, the new `alert_name` column on
`vertical_config` won't retroactively appear — either drop the dev
volume (`docker-compose down -v`) and let `create_all` rebuild it, or run:

```sql
ALTER TABLE vertical_config ADD COLUMN alert_name TEXT;
ALTER TABLE vertical_config DROP CONSTRAINT uq_vertical_service;
ALTER TABLE vertical_config ADD CONSTRAINT uq_vertical_service_alert UNIQUE (vertical, service, alert_name);
```

This is exactly the kind of change Alembic would have handled for you
automatically — worth prioritizing the migration to Alembic before the
next schema change if you're running against real data anyone depends on.

## Design notes worth re-reading before trusting this in production

1. **Fingerprinting excludes host/pod by default.** If any of your alert
   types genuinely need per-host tracking (e.g. "this specific node keeps
   flapping"), add the alert name to `HOST_SENSITIVE_ALERT_NAMES` in
   `app/fingerprinting/fingerprint.py`.
2. **Parser format profiles are minimal.** Only one default profile exists.
   As you connect real channels, add a per-channel entry to
   `CHANNEL_PROFILES` in `app/parsing/profiles.py` matching that channel's
   actual alert message format.
3. **`recommendations_log.human_reaction` exists but nothing populates it
   yet** — wiring up reaction-based feedback (🔥 = escalated, 👍/👎 on
   recommendation quality) is a good next small addition before Phase 2.
4. **Schema changes should move to Alembic** once you start iterating on
   this with a team, so schema history is versioned instead of implicit
   via `create_all`.

## Phase 5 additions — production hardening

1. **Alembic migrations** (`/migrations`) — schema is now versioned,
   closing out the note directly above. The initial migration
   (`0001_initial`) captures the full schema as of Phase 4. `init_db()`
   in `app/db/session.py` is now only a missing-table safety net
   (`checkfirst=True`) for local dev that hasn't run migrations yet — it
   does **not** alter existing tables, so don't rely on it for real
   schema changes anymore. `docker-compose.yml` now runs a `migrate`
   service (`alembic upgrade head`) before the app starts. To create a
   new migration after changing `app/db/models.py`:
   ```bash
   docker-compose run --rm app alembic revision --autogenerate -m "describe the change"
   docker-compose run --rm app alembic upgrade head
   ```
2. **Structured JSON logging** (`app/observability/logging_config.py`) —
   every log line is a JSON object (timestamp, level, logger, message),
   directly queryable in CloudWatch/Datadog/Loki without a parsing step.
3. **Prometheus metrics** at `GET /metrics` (`app/observability/metrics.py`)
   — alerts processed, classification breakdown, notifications sent,
   incidents created, and end-to-end pipeline latency.
4. **Dockerfile hardening** — runs as a non-root user (uid 1000), adds a
   container `HEALTHCHECK` against `/health`, and now bundles
   `/migrations` + `alembic.ini` so the image can run its own migrations.
5. **`.gitignore`** — added (was missing). Also fixes an issue from a
   prior upload: an `.env.example` had **real Slack credentials
   hardcoded into it** rather than placeholders at one point — confirm
   your working copy only has placeholders, and if it ever had real
   values, rotate that bot token and signing secret in your Slack app
   settings as a precaution, since `.env.example` is meant to be safe to
   commit. `.env` and `k8s/secret.yaml` (if created from the template)
   are both gitignored.
6. **CI** (`.github/workflows/ci.yml`) — on every push/PR: lint with
   ruff, run `alembic upgrade head` against a throwaway Postgres service
   container, run the full test suite against it, then build the Docker
   image. This is the first point the test suite runs against real
   Postgres rather than only the pure-logic subset — worth watching the
   first CI run closely in case anything behaves differently than it did
   under SQLite locally.
7. **Kubernetes manifests** (`/k8s`) — a basic `Deployment` + `Service`,
   a `ConfigMap` for non-secret settings, and a **secret template**
   (`secret.example.yaml`, explicitly not meant to be filled in and
   committed — use `kubectl create secret` or a real secrets manager
   instead). The deployment is pinned to **1 replica** with a comment
   explaining why: the Slack duplicate-delivery guard
   (`dedup_guard.py`) is still in-memory/process-local — scaling past 1
   replica needs that moved to Redis first, or duplicate Slack
   deliveries will double-count across replicas.

### What's still open after Phase 5

- The in-memory dedup guard and notification-cooldown state
  (`fingerprint.last_notified_at`) both assume a single app instance —
  fine at 1 replica, a real gap the moment you scale out
- No rate limiting on `/internal/events` — **now fixed, see below**
- ~~`triage_rules` table exists in the schema but the triage engine still
  reads its `DEFAULT_RULES` Python constant, not the DB rows~~ — **now
  fixed, see below**
- Load testing / HA (multi-replica Postgres, read replicas) hasn't been
  exercised at all — this hardening pass covers baseline production
  hygiene, not scale

## Post-Phase-5: auth, rate limiting, DB-driven triage rules

1. **API key auth** (`app/security/auth.py`) — set `INTERNAL_API_KEY` in
   `.env` (generate with `openssl rand -hex 32`) and every mutating or
   sensitive endpoint now requires an `X-API-Key` header matching it:
   `GET`/`POST /config/verticals`, `GET`/`POST /config/triage-rules`,
   `POST /internal/events`, `GET /incidents`,
   `POST /incidents/{id}/resolve`. `GET /health`, `GET /metrics`,
   `GET /dashboard`, and the per-fingerprint stats/history endpoints stay
   open (health/metrics need to work for k8s probes and monitoring
   without a key; dashboard/stats are read-only diagnostics — put them
   behind a reverse-proxy/SSO layer if you need to restrict them too).
   **If `INTERNAL_API_KEY` is left blank, auth is a no-op** — fine for
   local dev, and startup logs a loud warning so this can't go unnoticed
   in a real deployment.
2. **Rate limiting** (`app/security/rate_limit.py`) — `POST
   /internal/events` is limited to `RATE_LIMIT_REQUESTS_PER_MINUTE`
   (default 60) per API key, returning `429` past that. Same in-memory,
   process-local pattern as the dedup guard, with the same caveat: move
   to Redis before scaling past 1 replica.
3. **DB-driven triage rules** (`app/triage/rules_repository.py`) — the
   engine now reads from the `triage_rules` table via `GET`/`POST
   /config/triage-rules` (both require the API key) instead of the
   hardcoded `DEFAULT_RULES` constant, closing the gap noted after Phase
   5. The table auto-seeds from `DEFAULT_RULES` on first startup if
   empty, so behavior is unchanged until you actually add/edit rules.
   Reads are cached for 15 seconds to avoid a DB round-trip per alert;
   `POST /config/triage-rules` explicitly invalidates that cache so
   changes take effect within the same request cycle, not after a
   15-second lag. Condition keys must match
   `app.triage.triage_engine.SUPPORTED_CONDITIONS` — an unsupported key
   raises at classification time (a genuine alert), not at rule-creation
   time, so validate against that list before adding a rule.

Example: tighten the noise threshold via the API instead of a redeploy:
```bash
curl -X POST http://localhost:8000/config/triage-rules \
  -H "X-API-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "rule_name": "stricter_noise",
    "classification": "potential_noise",
    "priority": 25,
    "condition": {"occurrences_24h_gt": 10, "avg_interval_seconds_lt": 900}
  }'
```

## Dashboard upgrade: vertical trends, filters, and direct actions

The dashboard (`GET /dashboard`) is now a live, filterable view rather
than a static per-load snapshot.

**What's real vs. derived -- read this before trusting the numbers:**

- **Priority (P0-P3)** is *not* a field that's ever been collected --
  there's only `severity` (CRITICAL/WARNING/WARN/INFO, set by the alert
  parser). `severity_to_priority()` in `app/dashboard/queries.py` maps
  CRITICAL→P0, WARNING/WARN→P1, INFO→P2, unknown→P3. This is a display
  convention layered on for the dashboard; it doesn't feed back into
  triage, routing, or Jira priority anywhere.
- **"Avg. Triage Processing Time"** means alert-ingested →
  notification-sent latency (`AlertEvent.occurred_at` →
  `RecommendationLog.notified_at`), the closest thing actually tracked
  per alert. It is **not** the same as incident resolution time
  (`Incident.opened_at` → `closed_at`, a human action) -- both are
  legitimate readings, this one specifically covers automated triage
  latency.
- **"Suppressed / Deduplicated"** is two genuinely different numbers
  combined into one headline figure:
  - `cooldown_suppressed` -- alerts that reached the pipeline and got an
    `AlertEvent` row, but were blocked from notifying by the anti-noise
    cooldown. Computed from the database, scoped to whatever time range
    is selected.
  - `deduplicated` -- Slack event *retries* dropped by the dedup guard
    (`app/ingestion/dedup_guard.py`) **before** they ever become a DB
    row at all. Not queryable from the database for that reason -- this
    comes from a new Prometheus counter
    (`alert_triage_alerts_deduplicated_total`) instead, which is
    **process-wide since the last restart**, not scoped to the selected
    time range, and (like the dedup guard itself) resets on restart and
    doesn't coordinate across multiple replicas. Hover the card in the
    UI for the breakdown.
- **Correlated-alert counts and LLM summaries in the incident feed**
  required two small additions: a new `llm_explanation` column on
  `recommendations_log` (migration `0002_llm_explanation`, since this
  text was previously composed into the Slack message and then
  discarded -- never persisted anywhere), and live recomputation of
  `find_related_alerts()` per incident row at render time (correlation
  results were never persisted either; fine at dashboard-page volumes).

**New backend pieces:**
- `app/dashboard/queries.py` -- `alert_volume_by_vertical_over_time()`,
  `priority_distribution_by_vertical()`,
  `avg_processing_seconds_by_vertical()`, `summary_cards()`,
  `incident_feed()`, all dialect-aware (SQLite/Postgres) like the
  existing frequency-trend query.
- `app/dashboard/routes.py` -- `GET /dashboard` still server-renders the
  first paint (works with JS disabled); `/dashboard/api/*` JSON
  endpoints back the interactive filters/charts and are read-only +
  unauthenticated, consistent with the rest of the read-only diagnostics
  in this app.
- `app/dashboard/actions.py` -- `reevaluate_fingerprint()` re-runs
  stats + triage + (possibly) notification on demand for one fingerprint,
  reusing the exact same steps the normal pipeline uses rather than a
  separate code path. `sync_jira_status()` pulls an incident's local
  `status` back in line with Jira's real status category (for tickets
  resolved directly in Jira rather than through this app) via a new
  `JiraClient.get_issue_status_category()` method. Both require the API
  key (`POST /dashboard/actions/{id}/reevaluate`,
  `POST /dashboard/actions/{id}/sync-jira}`), same as `/config` and
  `/incidents` -- read-only diagnostics stay open, anything that mutates
  state needs the key.
- **Routing-rule tweaks** happen via a form on the dashboard that POSTs
  straight to the existing `POST /config/verticals` endpoint -- no new
  endpoint needed. One thing worth remembering (learned the hard way
  during testing): that endpoint always **inserts a new row**, it never
  updates one in place. Submitting the form twice for the same
  vertical/service/alert_name creates two rows, not an overwrite --
  check `GET /config/verticals` if routing ever looks like it's serving
  a stale value after an edit.

**Testing this locally:** `alembic upgrade head` picks up the new
`llm_explanation` column automatically. `pytest tests/ -v` now includes
`test_dashboard_queries.py`, covering the priority mapping and the
cooldown/dedup split specifically (the split is easy to accidentally
re-merge back into one number in a future edit, hence a test pinning
that contract).

## Multi-channel support + auth UX fix

**Auth UX fix:** the dashboard's action buttons (re-evaluate, sync Jira,
save routing rule) previously blocked client-side with "Enter the API
key above" whenever the key field was empty -- even when
`INTERNAL_API_KEY` isn't set server-side at all, where no key is
actually required. Fixed: the UI now always attempts the action; the
server is the single source of truth for whether a key is required, and
its actual 401 (if any) surfaces as the error message instead of the
frontend guessing preemptively.

**Multi-channel alert identification:** the Slack listener already
processed messages from every channel the bot is invited to -- nothing
needed to change there. The real gap: vertical/service identification
came ONLY from text inside the alert message (e.g. `vertical: payments`
lines), never from which channel it arrived in. If your alerting tool's
Slack template doesn't include those exact fields (true for most tools'
default formats), the alert would still get parsed, fingerprinted, and
stored -- but then match nothing in `resolve_routing()` and silently
never get notified, no error anywhere.

Fixed in `app/routing/router.py::infer_vertical_service_from_channel()`,
wired into the pipeline right after parsing (`app/ingestion/pipeline.py`,
step 1b) as a fallback used only when the message text didn't specify a
vertical. It reuses the existing `vertical_config.alert_channel` field
(which recorded "where alerts are read from" per the original schema
design, but was never actually read anywhere until now):

- If exactly one `vertical_config` row is registered for a channel and
  it has a `service` set, both vertical and service are inferred (the
  channel uniquely identifies one service).
- If multiple rows share a channel with different services, only the
  vertical is inferred (service can't be told apart by channel alone in
  that case).
- If nothing is configured for that channel, no inference happens and
  the alert falls through to whatever the message text (or nothing)
  provided, same as before this fix.

**What this means for setting up additional channels:** for every new
channel you invite the bot to, add a `vertical_config` row with
`alert_channel` set to that channel's real Slack ID (same "use the ID,
not the name" rule from the `channel_not_found` debugging earlier) --
even if you're not changing where notifications go, this row is now
what teaches the bot which vertical/service that channel's alerts
belong to when the message text itself doesn't say. One channel per
service gets you full auto-identification; one channel shared across
several services in a vertical still gets correctly routed at the
vertical level, just not disambiguated down to the exact service from
channel alone.

Covered in `tests/test_routing.py` (four new cases: unique
channel→service, channel→vertical-only, ambiguous shared channel, and
unconfigured channel).

## Real Alertmanager format support

Your production alerts (via Alertmanager) look nothing like the toy
test format this project used throughout development. Added a second
parsing profile (`app/parsing/profiles.py::ALERTMANAGER_PATTERNS`),
auto-detected by content (a `[FIRING:N]`/`[RESOLVED:N]` signature) so it
works regardless of which channel the alert lands in -- no per-channel
config needed unless you have a second, differently-formatted source on
top of this.

**Field mapping I inferred from one sample alert -- please verify this
matches your org's actual semantics:**

| Field | Source | Example |
|---|---|---|
| `alert_name` | Text right after `[FIRING:N]`/`[RESOLVED:N]` | `HostHighCpuLoad` |
| `service` | Annotations `Application` | `postprocessing_pg2` |
| `vertical` | Labels/title `techteam` | `pplusprod` |
| `environment` | Annotations `Environment` | `rtdd` |
| `severity` | `severity: critical` (title or Labels) | `CRITICAL` |
| `priority` | `priority: P0` (title only, in the sample) | `P0` |
| `host_or_pod` | `instance:` | `10.210.9.231:9100` |

**If `techteam`→vertical / `Application`→service is backwards for how
your org actually thinks about it** (e.g. if `techteam` is really your
service-level identifier), swap the two regexes in
`ALERTMANAGER_PATTERNS` -- nothing downstream (fingerprinting, routing,
triage, dashboard) has any independent opinion about which label means
what; they just use whatever ends up in `vertical`/`service`.

**Explicit priority is now a real, stored field**, not just derived from
severity. `AlertEvent.priority` (migration `0003_alert_priority`) holds
the alert's own `P0`-style value when the source provided one; the
dashboard's `severity_to_priority()` mapping is now only a fallback for
alerts that don't state a priority explicitly (`effective_priority()` in
`app/dashboard/queries.py` picks whichever is available, real value
always winning). The Slack message itself now also shows priority when
present.

**Before this reaches your real alert channels for real:** run
`alembic upgrade head` to pick up the new column, then post this exact
sample alert (or a real one) through and confirm in the dashboard that
service/vertical/priority show up as expected -- `tests/test_parser.py`
pins the regex behavior against this exact message, but only you can
confirm the *semantic* mapping (techteam→vertical) is actually right for
your org.

## Channel-to-vertical identification: dedicated table, not overloaded config

Follow-up to the previous round's channel-inference feature: reusing
`vertical_config.alert_channel` for identification had a real structural
bug -- that table's uniqueness is `(vertical, service, alert_name)`,
which has no room for two different channels (e.g. "pg-aws" and
"pg-canary") both mapping to the same vertical with no service override
-- the second insert would violate the unique constraint.

**Fixed with a dedicated table**, `source_channels` (migration
`0005_source_channels`), where `channel_id` itself is the unique key --
many rows can freely share one `vertical` value, which is exactly the
"many channels, one vertical" shape you described. `VerticalConfig` goes
back to being purely about notification *routing* (where to send), not
identification (which vertical is this from) -- the `alert_channel` /
`channel_name` columns there are now vestigial, left in place to avoid a
destructive migration but no longer read for identification anywhere.

**New endpoints** (`app/main.py`):
- `GET /config/channels` -- list all channel→vertical mappings
- `POST /config/channels` -- **upsert** on `channel_id` (unlike
  `/config/verticals`, which is insert-only and has bitten us twice now
  during testing with duplicate rows) -- posting the same channel again
  updates it in place instead of creating a duplicate

**Dashboard**: new "Channel → Vertical Mapping" section (below Routing
Config) with a form and a live table of current mappings. The Top-10
alerts table and the Incident Feed both now show which channel each
alert actually arrived in (`app/dashboard/queries.py` was already
computing this from a previous pass; it just wasn't rendered in the
template until now).

**Resolution order in the pipeline unchanged from last round**: channel
mapping (now via `source_channels`) is checked first and is
authoritative when present, overriding whatever the alert text itself
claims; text-based parsing (`techteam` field, etc.) is the fallback only
for channels not yet registered.

**To set up your actual channels**: for `pg-aws` belonging to `pg2.0`,
add one mapping (`channel_id` = pg-aws's real Slack ID, `vertical` =
"pg2.0", `service` left blank since it covers the whole vertical). Add a
second mapping for any other channel that also belongs to `pg2.0` --
this is now the exact scenario the new table was built to support
without conflict. `tests/test_routing.py` includes a test asserting this
specific two-channels-one-vertical case commits without error.

Run `alembic upgrade head` to pick up the new table before testing this.
