# BrevitySisterBot 0.2.0: production architecture on Beget

## 1. Status and purpose

This document defines the approved architecture for rewriting BrevitySisterBot
as release `0.2.0`.

The current implementation is treated as MVP `0.1.0`. During implementation it
will be moved, unchanged in behavior, into `mvp-1/`. The repository root will
then contain the new `0.2.0` application.

This is a clean rewrite rather than an incremental conversion. The rewrite is
appropriate because the production runtime changes fundamentally:

- Telegram long polling becomes a webhook;
- local SQLite storage becomes MySQL;
- machine-local cron orchestration becomes an event-driven request path with
  Beget recovery and scheduled jobs;
- a local Ollama model becomes an external Groq model;
- private summary requests and multi-group selection are added.

The target is a small production service for up to 20 Telegram groups. The
initial real workload is two groups, of which one group currently contains live
data.

## 2. Product behavior

### 2.1 Group collection

The bot receives every Telegram update through an HTTPS webhook. A group
message is persisted to MySQL during the webhook request; there is no periodic
message collection and no five-minute collection lag.

Only groups that have been connected with `/enable_daily` are tracked.
Disabling daily publication stops scheduled summaries but does not erase stored
membership or historical configuration.

`/enable_daily` and `/disable_daily` may be used only by a current group
administrator. The bot verifies the role through Telegram `getChatMember`.

### 2.2 Scheduled summary

All groups use one schedule in `Europe/Moscow` for version `0.2.0`:

- final preparation starts at `20:45`;
- publication occurs at `21:00`.

The automatic summary is generated only when at least 15 eligible messages
exist for the current day. If there are fewer messages, the bot does not call
the LLM and does not publish an automatic summary.

Per-group publication times are outside the scope of `0.2.0`.

### 2.3 On-demand summary inside a group

Any current member of a tracked group may request `/summary` in that group. The
result is visible to the whole group.

An on-demand summary requires at least five eligible messages. With fewer than
five messages, the bot responds that there is not yet enough material for a
meaningful summary.

If a suitable summary has already been generated for the same source-message
watermark, it may be returned from cache. Otherwise the bot creates a current
summary from all messages persisted up to the request.

An on-demand summary must not overwrite or prematurely publish the scheduled
21:00 summary.

### 2.4 Private on-demand summary

Users may request `/summary` in a private chat with the bot.

The bot maintains a relationship between Telegram users and tracked groups
based on messages and commands observed in those groups. A user establishes
the initial relationship by invoking `/summary` in the group at least once.

Before returning private group data, the bot verifies current membership
through Telegram `getChatMember` for every private request or group-selection
callback:

- if the user has access to one tracked group, the bot summarizes it directly;
- if the user has access to several groups, the bot shows inline buttons for
  group selection;
- if no current membership can be verified, no group summary is disclosed.

The private request and its result are visible only in the private chat.

### 2.5 User-facing processing message

For an operation that may require LLM generation, the bot immediately sends:

> Запрос принят. Подготовка может занять до 1–2 минут. Пожалуйста, не
> отправляйте команду повторно.

If the same user repeats an equivalent request while it is active, the bot
reports that the summary is already being prepared instead of creating another
generation job.

Normal processing begins during the webhook request and should usually finish
within seconds. The five-minute cron interval is a recovery guarantee, not the
normal response time.

## 3. Chosen deployment architecture

### 3.1 Hosting constraint

Both BrevitySisterBot and BernsBot run on Beget shared hosting. A VPS,
always-running polling daemon, Docker Compose deployment, and server-managed
message broker are not required.

The applications share operational conventions but remain isolated:

- separate domains or subdomains;
- separate deployment directories;
- separate Telegram bot tokens;
- separate webhook secret tokens;
- separate MySQL databases;
- separate environment files, logs, backups, and preview environments.

No application may read or write the other application's production database.

### 3.2 Runtime components

The `0.2.0` application consists of:

1. **WSGI webhook application**
   - hosted through Passenger;
   - accepts only Telegram `POST` requests;
   - validates `X-Telegram-Bot-Api-Secret-Token`;
   - persists the update before business processing;
   - attempts immediate processing;
   - returns a successful Telegram webhook response without requiring a
     permanently running process.

2. **Update processor**
   - claims queued updates idempotently;
   - handles commands, messages, membership observations, and callbacks;
   - records processing state, attempts, and errors;
   - safely retries interrupted work.

3. **Summary service**
   - reads eligible messages for a chat and time window;
   - builds a bounded transcript;
   - calls the configured LLM provider;
   - validates and normalizes structured output;
   - performs at most one repair call when useful;
   - falls back to the existing deterministic heuristic.

4. **Telegram client**
   - sends acknowledgements, results, errors, and group-selection buttons;
   - checks administrators and current group membership;
   - retries safe Telegram operations without duplicating publication.

5. **Recovery worker**
   - runs every five minutes through Beget CronTab;
   - claims pending, retryable, or stale processing jobs;
   - does not rescan Telegram or collect messages;
   - exits quickly when the queue is empty.

6. **Scheduled commands**
   - prepare scheduled summaries;
   - publish scheduled summaries;
   - archive completed days;
   - clean safely archived working data;
   - purge expired archive rows;
   - create and rotate database backups;
   - run a daily operational audit.

### 3.3 Why this approach was selected

Three approaches were considered:

1. **Python WSGI webhook, MySQL, immediate processing, five-minute recovery
   cron** — selected. It keeps canonical business logic in Python, records
   messages immediately, and remains compatible with Beget shared hosting.
2. **PHP webhook plus Python cron processor** — viable but imposes normal
   queueing delay and adds a second runtime boundary.
3. **Complete PHP rewrite** — compatible with the current BernsBot production
   pattern but would duplicate or replace the mature Python summarizer.

The selected design uses cron only for recovery and exact scheduled work.

## 4. Data model

One physical MySQL database is used for BrevitySisterBot. Working and archive
data are separated by tables, not by databases.

### 4.1 Core tables

#### `telegram_updates`

Durable webhook inbox and processing queue.

Key fields:

- `update_id` — Telegram update identifier and unique key;
- `payload_json` — original update required for retry;
- `status` — `pending`, `processing`, `completed`, `retry`, or `failed`;
- `attempt_count`;
- `available_at`;
- `claimed_at`;
- `completed_at`;
- `last_error_code`;
- `last_error_message`;
- `created_at`.

Duplicate Telegram deliveries are accepted but do not create duplicate work.

#### `tracked_chats`

Group configuration.

Key fields:

- `chat_id` — Telegram chat ID and primary key;
- `title`;
- `enabled`;
- `timezone`, initially always `Europe/Moscow`;
- `scheduled_publish_time`, initially always `21:00`;
- `created_at`;
- `updated_at`.

#### `chat_members`

Observed user-to-group relationships used for private summary discovery.

Key fields:

- `chat_id`;
- `user_id`;
- display metadata;
- `first_seen_at`;
- `last_seen_at`;
- `last_membership_check_at`;
- `last_membership_status`.

This table is a discovery cache, not authorization. Private disclosure always
requires a Telegram membership check for the current request or selection
callback.

#### `messages`

Temporary working messages.

Key fields:

- internal ID;
- `chat_id`;
- `telegram_message_id`;
- sender metadata;
- text;
- `sent_at`;
- `created_at`.

The unique key is `(chat_id, telegram_message_id)`.

#### `prepared_summaries`

Generated summaries and their lifecycle.

Key fields:

- internal ID;
- `chat_id`;
- `summary_date`;
- `summary_kind` — `scheduled` or `on_demand`;
- `requester_user_id`, nullable for scheduled summaries;
- source-message watermark and deterministic source hash;
- summary text;
- `status` — `preparing`, `prepared`, `sending`, `sent`, `retry`, or `failed`;
- provider, model, latency, token or character counts, and fallback type;
- `prepared_at`;
- `sent_at`;
- error metadata.

A scheduled summary has a unique logical identity of
`(chat_id, summary_date, summary_kind)`. On-demand summaries use
`(chat_id, summary_date, summary_kind, source_hash)` for deduplication and
caching. The requester is recorded for delivery and audit but does not force a
second LLM call for the same chat state.

#### `scheduled_publications`

Idempotent publication outbox.

Key fields:

- `chat_id`;
- `summary_date`;
- prepared summary ID;
- `status`;
- `attempt_count`;
- Telegram result metadata;
- `sent_at`.

The unique key `(chat_id, summary_date)` prevents the 21:00 summary from being
published twice.

#### `archived_messages`

Thirty-day copy of successfully completed daily message data.

The unique key remains `(chat_id, telegram_message_id)`.

#### `archived_summaries`

Thirty-day archive of generated summaries and publication metadata. Repeated
archive runs update only mutable operational metadata and never erase a valid
summary with an empty value.

#### `daily_lifecycle`

Per-chat safety gate for archive and cleanup.

Key fields:

- `chat_id`;
- `summary_date`;
- message count and checksum/watermark;
- `summary_prepared_at`;
- `summary_sent_at`, nullable if below threshold;
- `archive_completed_at`;
- archived message count and verification result;
- `working_cleanup_completed_at`.

#### `job_locks`

Database-backed short leases for scheduled and recovery tasks. Locks use an
expiry time so a terminated process cannot block work permanently.

#### `schema_migrations`

Applied migration versions and timestamps.

## 5. Data safety and retention

The governing rule is: temporary duplicates are acceptable; silent loss or
accidental overwrite is not.

### 5.1 Update and message safety

- The webhook commits `telegram_updates` before processing.
- `update_id` prevents duplicate update execution.
- `(chat_id, telegram_message_id)` prevents duplicate message insertion.
- Update processing uses a transaction for local state changes.
- A stale `processing` update is returned to `retry` after its lease expires.
- Permanent failure retains the payload and error metadata for manual
  inspection during the retention window.

### 5.2 Summary safety

- Scheduled and on-demand summaries have different identities.
- A manual summary never overwrites the scheduled summary.
- A new generation is written as a new record or a safe state transition; an
  empty or failed result never replaces a valid prepared summary.
- Publication uses an outbox record and a unique daily key.
- Retry logic checks publication state before sending again.

### 5.3 Archive and cleanup sequence

Working messages are not moved destructively. The daily sequence is:

1. copy eligible rows to archive with idempotent inserts;
2. compare expected and archived message identifiers and counts;
3. mark `archive_completed_at`;
4. in a separate cleanup job, delete working rows only when lifecycle gates are
   satisfied;
5. record cleanup completion.

If the summary is below the automatic threshold, the lifecycle records an
intentional no-publication outcome and still permits verified archive and
cleanup.

If Telegram publication, Groq generation, or archive verification fails,
working messages remain available for retry.

### 5.4 Retention

- working messages: retained until the day's archive is verified and cleanup is
  safe;
- archived messages and summaries: 30 days;
- durable update payloads and job diagnostics: 30 days unless a shorter safe
  retention is selected during implementation;
- MySQL backups: at most 30 days;
- valid application logs: rotated and retained for no more than 30 days.

Deletion uses strict cutoffs such as `created_at < cutoff`, runs in bounded
batches, and is independent from the archival transaction.

### 5.5 Backups and migrations

- MySQL backups run twice daily and immediately before production migrations.
- Backups are stored outside `public_html`.
- Backup rotation removes files older than 30 days.
- Deployment never uploads a database dump over production data.
- Schema changes are versioned migrations.
- Migrations for `0.2.x` should be forward-compatible with the previous
  application code whenever practical, allowing a code rollback without a
  database rollback.

## 6. LLM design

### 6.1 Provider and model

The primary provider is Groq Free API with Zero Data Retention enabled.

The initial model is:

```text
qwen/qwen3.6-27b
```

The model name is configured through `GROQ_MODEL`; it is not hard-coded into
business logic.

The provider API key is stored only in the Beget environment file.

### 6.2 Provider boundary

The summary service depends on an internal LLM client interface rather than
Groq-specific calls. A provider implementation returns:

- raw or structured output;
- provider and model identifiers;
- duration;
- token usage when available;
- normalized error category.

This permits later replacement by another free or paid provider without
changing summary orchestration.

### 6.3 Prompt and transcript limits

The existing structured Russian summary style and heuristic quality controls
are retained.

The transcript is bounded by token and character budgets. When a busy group
exceeds the budget, messages are reduced deterministically while retaining:

- chronological coverage;
- repeated topics;
- participant attribution;
- recent decisions and unresolved questions.

The first implementation may use a bounded recent transcript plus deterministic
sampling; recursive multi-pass summarization is outside the initial scope
unless real group volume requires it.

### 6.4 Failure behavior

- LLM calls have explicit connect and total timeouts.
- Retry is bounded and used only for transient errors.
- `429` respects `Retry-After` when compatible with the request deadline.
- One repair request may be made for malformed structured output.
- After retries or invalid output, the existing heuristic summarizer produces a
  usable result.
- Approaching application-defined free-tier limits causes manual requests to
  use a fresh cache or heuristic summary rather than fail.

Free API capacity is not treated as an SLA. The product must remain functional
without Groq.

### 6.5 Privacy and logging

- Group transcripts are sent to Groq only for requested summary generation.
- Users are informed that an external AI service processes group messages.
- Full transcripts and full Groq responses are not stored in normal logs.
- Logs contain only operational metadata: provider, model, duration, input
  size, status, retry count, and fallback type.
- Tokens and webhook secrets are never logged.

## 7. Scheduling

Beget CronTab provides the following jobs in Moscow time:

- `*/5 * * * *` — recover pending, retryable, and stale update or summary jobs;
- `45 20 * * *` — prepare scheduled summaries;
- `0 21 * * *` — publish prepared summaries;
- `15 21 * * *` — archive and verify the completed day;
- `30 21 * * *` — clean working rows whose lifecycle gates are satisfied;
- `50 11,23 * * *` — create a MySQL backup;
- `15 3 * * *` — purge expired archive rows, diagnostics, logs, and backups;
- `0 9 * * *` — run the operational audit and notify the owner.

Failed scheduled work is also represented as retryable jobs, so the five-minute
recovery worker can complete publication, archive, or cleanup after the nominal
minute. Cleanup is state-gated rather than blindly time-gated.

## 8. Security

- Webhook endpoints accept only `POST`.
- Telegram webhook secret validation occurs before payload processing.
- Invalid requests receive an error without exposing configuration.
- Group configuration commands require a verified administrator.
- Private summary disclosure requires verified current membership.
- Callback data uses short opaque identifiers and server-side authorization.
- Environment files, backups, logs, and migration credentials stay outside the
  web root.
- MySQL uses a dedicated user with access only to the BrevitySisterBot database.
- Production and preview use separate bots, secrets, directories, and
  databases.
- Raw group content is excluded from routine logs and owner alerts.

## 9. Observability and operations

### 9.1 Health checks

A non-secret health endpoint reports whether the application can start and
reach MySQL. It does not expose credentials, queue payloads, group names, or
message text.

### 9.2 Operational audit

The daily audit checks:

- counts in `pending`, `processing`, `retry`, and `failed`;
- age of the oldest unfinished update;
- stale job leases;
- scheduled summaries that were not published;
- archive verification failures;
- age and success of the latest backup;
- recent Groq failure and fallback rates.

Actionable failures produce a concise Telegram notification to the configured
owner chat.

### 9.3 Logging

Logs are structured enough to correlate:

- Telegram `update_id`;
- job ID;
- `chat_id` represented safely;
- summary ID;
- error category and attempt number.

Logs do not contain full message bodies or secrets.

## 10. Testing strategy

### 10.1 Unit tests

Cover:

- 5-message and 15-message thresholds;
- summary parsing, validation, repair, and heuristic fallback;
- transcript limits;
- administrator checks;
- current membership checks;
- private group selection;
- duplicate-command suppression;
- archive and cleanup eligibility;
- retention cutoff calculations.

### 10.2 Repository and migration tests

Run against an isolated MySQL test database and cover:

- unique update and message keys;
- update claiming and stale-lease recovery;
- transactional state transitions;
- scheduled publication idempotency;
- archive copy and verification;
- cleanup gates;
- forward migration from an empty `0.2.0` database;
- import of the single live `0.1.0` group.

SQLite may remain in tests only where it tests pure legacy behavior inside
`mvp-1/`; production repositories target MySQL.

### 10.3 Contract tests

Mock Telegram and Groq for:

- duplicate webhook delivery;
- Telegram timeout and safe retry;
- Groq timeout;
- `429` with and without `Retry-After`;
- malformed JSON;
- valid structured output;
- removed or unavailable configured model.

### 10.4 End-to-end and failure tests

Cover:

- webhook to MySQL to processor to Telegram response;
- group message persistence without collection lag;
- private `/summary` with one and multiple groups;
- terminated processing followed by five-minute recovery;
- repeated scheduled jobs without duplicate publication;
- archive failure preserving working data;
- LLM outage producing a heuristic summary.

### 10.5 Preview smoke test

Before production:

- webhook succeeds with the preview secret and rejects an invalid secret;
- a preview group can be enabled only by an administrator;
- messages appear only in the preview database;
- group and private `/summary` work;
- group selection works;
- scheduled preparation and publication work;
- archive verification and cleanup work;
- backup is created outside the web root;
- production row counts do not change.

## 11. Repository and release structure

### 11.1 MVP preservation

The current repository-root implementation is preserved under:

```text
mvp-1/
```

The snapshot contains the `0.1.0` source, tests, documentation, and deployment
examples needed to understand or run the MVP. Runtime secrets, virtual
environments, logs, generated databases, local caches, and visualization
working files are not copied into the committed snapshot.

No behavior changes are made inside `mvp-1/` as part of the move.

### 11.2 New root

The new root contains the `0.2.0` application with clear boundaries:

```text
app/
  config/
  domain/
  services/
  repositories/
  integrations/
    telegram/
    groq/
  web/
  jobs/
migrations/
scripts/
tests/
docs/
passenger_wsgi.py
pyproject.toml
```

Final package names and file granularity are determined by the implementation
plan, while preserving these dependency directions:

- domain code does not import Telegram, Groq, WSGI, or MySQL drivers;
- services depend on interfaces;
- repositories and integrations implement those interfaces;
- webhook and cron commands are thin entry points.

### 11.3 Versioning

- historical implementation: `0.1.0`;
- rewritten first production release: `0.2.0`;
- schema and application version are reported independently;
- release flow follows preview, smoke test, release commit, tag, then production
  deployment.

## 12. Import and cutover

Only one group currently has live `0.1.0` data. The rewrite therefore uses a
controlled one-time import rather than a general dual-write migration.

The import:

1. creates immutable backups of `bot.db` and `archive.db`;
2. reads tracked chat configuration, working messages, prepared summaries, and
   archived rows;
3. writes them to `0.2.0` MySQL tables with idempotent keys;
4. compares counts and identifiers;
5. writes an import report;
6. does not delete or mutate the SQLite source files.

Cutover:

1. deploy and test preview with a separate bot;
2. deploy production code and migrate the empty production schema;
3. run and verify the one-time import;
4. stop old polling and scheduled collection jobs;
5. configure the production webhook;
6. verify webhook info and a live group message;
7. keep SQLite backups for at least the 30-day stabilization window;
8. enable automated cleanup only after a complete successful daily cycle.

Telegram long polling and webhook delivery must never run concurrently for the
same production bot.

## 13. Out of scope for 0.2.0

- per-group publication time;
- more than one timezone;
- web administration dashboard;
- paid subscription or billing;
- vector search or semantic long-term memory;
- summaries older than the 30-day archive window;
- processing non-text media beyond captions;
- distributed brokers such as Redis, RabbitMQ, or Kafka;
- multi-region or horizontally scaled runtime.

## 14. Acceptance criteria

Release `0.2.0` is ready when:

1. Telegram messages are persisted immediately through the webhook.
2. Repeated updates, jobs, imports, and cron runs do not duplicate messages or
   scheduled publications.
3. A process interruption cannot lose a committed update.
4. Working messages are never deleted before verified archive completion.
5. Groq `qwen/qwen3.6-27b` generates valid Russian summaries, with deterministic
   fallback when unavailable.
6. Automatic summaries use the 15-message threshold and publish at 21:00 MSK.
7. Group and private summaries use the five-message threshold.
8. Private summaries disclose data only after current membership verification.
9. Only administrators can enable or disable group summaries.
10. Archive, logs, update diagnostics, and backups respect the 30-day maximum
    retention.
11. Preview is fully isolated from production.
12. The single live MVP group is imported with verified counts and identifiers.
13. All automated tests and the preview smoke checklist pass.
14. BernsBot continues operating independently throughout deployment.
