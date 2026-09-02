# BrevitySisterBot 0.3.0 DeepSeek Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the private-summary UX fixes, replace Groq with `deepseek-v4-flash`, persist actual token/cost telemetry, and send one idempotent weekly budget report.

**Architecture:** Keep the synchronous WSGI webhook, durable Telegram effects, MySQL summary cache, and local heuristic fallback. A standard-library DeepSeek client supplies strict JSON summaries and balance data; additive MySQL fields aggregate every initial/repair call per prepared summary, while a focused usage service builds CLI and weekly Telegram reports from stored telemetry and frozen balance snapshots.

**Tech Stack:** Python 3.10, WSGI/Phusion Passenger, MySQL/PyMySQL, Telegram Bot API, DeepSeek OpenAI-compatible HTTP API, pytest, Ruff, TOML copy catalog, Beget cron.

## Global Constraints

- Release version is exactly `0.3.0`.
- Preserve commit `7abe036` behavior: successful `/enable_daily` remembers the enabling administrator and `ChatRepository.tracked_count()` counts every tracked group regardless of the daily `enabled` flag.
- Plain private `/start` sends a welcome message and button but does not generate a summary.
- `/start summary_<token>` remains a direct request for the token's group.
- A single known group is auto-selected only when `tracked_count() == 1`; multiple known groups are shown without preliminary Telegram membership calls; ambiguous cases use native `request_chat`.
- Send `summary_accepted` after a group is selected but before `getChatMember` and before model generation; add no artificial delay.
- Production LLM provider is only DeepSeek; do not silently fall back to Groq.
- Default model is exactly `deepseek-v4-flash` with `thinking.type = disabled`, `response_format.type = json_object`, `temperature = 0.2`, `max_tokens = 1200`, `stream = false`, and a 90-second client timeout.
- Keep the deterministic local heuristic summary for DeepSeek failure or unusable JSON.
- Do not add the OpenAI SDK or another runtime dependency.
- Never expose API keys in git, database rows, exceptions, logs, Telegram messages, repr output, or test snapshots.
- Cost is stored in integer micro-USD using DeepSeek Flash pricing effective 2026-08-16 and the documented UTC peak windows.
- Weekly report runs Sunday at `21:30 Europe/Moscow`, uses `OWNER_CHAT_ID`, freezes its text per week, and is durable/idempotent.
- Migrations are additive and restart-safe; no existing table, column, summary, message, or archive row may be deleted or overwritten.
- Keep `content/bot_copy.ru.toml` as the only source of user-facing bot copy.
- Do not modify `mvp-1/`.

## Existing completed work

Commit `7abe036` already completed and tested the first private-flow prerequisite:

- `ChatRepository.tracked_count() -> int`;
- MySQL `COUNT(*)` over every `tracked_chats` row;
- successful `/enable_daily` calls `remember_group(...)` before onboarding copy.

Do not reimplement or revert it. Task 1 starts at the next uncompleted behavior.

---

### Task 1: Complete plain `/start` and safe multi-group choice

**Files:**
- Modify: `content/bot_copy.ru.toml`
- Modify: `docs/BOT_COPY_RU.md`
- Modify: `app/services/on_demand.py`
- Modify: `tests/e2e/test_private_summary.py`
- Modify: `tests/e2e/test_reservation_recovery.py`
- Modify: chat repository fakes in tests that instantiate `OnDemandSummaryHandler`

**Interfaces:**
- Consumes: `ChatRepository.tracked_count() -> int` from commit `7abe036`.
- Produces: callback data `summary_start`.
- Produces copy keys: `messages.private_welcome` and `buttons.start_summary`.
- Preserves: `summary_group:<opaque-token>` and native `request_chat` selection.

- [ ] **Step 1: Write RED tests for plain `/start` and its button**

In `tests/e2e/test_private_summary.py`, add:

```python
def test_plain_private_start_welcomes_without_generating() -> None:
    handler, _, _, _, telegram, summaries, _ = make_handler()

    handler(private_message("/start"))

    assert telegram.sent == [
        (
            42,
            COPY.value("messages", "private_welcome"),
            {
                "inline_keyboard": [[{
                    "text": COPY.value("buttons", "start_summary"),
                    "callback_data": "summary_start",
                }]]
            },
            None,
        )
    ]
    assert summaries.calls == []
```

Add a callback update with `data="summary_start"`; assert the callback is
answered and, with no known groups, the last message is `choose_group_native`.

- [ ] **Step 2: Write RED tests for the complete group-choice table**

Extend `FakeChats` with `tracked_group_count = 0` and:

```python
def tracked_count(self):
    return self.tracked_group_count
```

Record every `(chat_id, user_id)` in `FakeTelegram.membership_calls`. Add:

```python
def test_one_known_group_uses_native_picker_when_another_group_is_tracked():
    # groups=[(-1001, "One")], tracked_group_count=2
    # Expect choose_group_native, no membership calls, no generation.

def test_one_known_group_is_direct_only_when_it_is_the_only_tracked_group():
    # groups=[(-1001, "One")], tracked_group_count=1, allow_group(...)
    # Expect generation for -1001.

def test_multiple_known_groups_show_buttons_without_membership_calls():
    # groups has two items and tracked_group_count=2.
    # Expect two opaque inline buttons, membership_calls == [], no generation.
```

- [ ] **Step 3: Run the RED tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/e2e/test_private_summary.py -q
```

Expected: failures for missing copy keys, missing `summary_start` routing, and
the old eager membership/single-group behavior.

- [ ] **Step 4: Add the catalog copy and plain `/start` route**

Add exactly:

```toml
private_welcome = "Привет! Я готовлю краткие сводки по сообщениям из подключённых групп. Нажмите кнопку ниже или отправьте /summary."
```

```toml
start_summary = "Получить сводку"
```

Keep `_private_start_token()` handling before the plain command branch. For a
plain `/start`, send one inline button with `callback_data="summary_start"` and
return. In `_handle_callback`, add before `summary_group:` handling:

```python
if callback.data == "summary_start":
    self.telegram.answer_callback_query(callback.callback_id)
    self._handle_private_summary(
        update_id=update.update_id,
        user_id=callback.user_id,
        username="",
        full_name="",
        destination_chat_id=callback.chat_id,
    )
    return
```

- [ ] **Step 5: Implement the choice decision table without eager API checks**

Replace the filtered list comprehension in `_handle_private_summary` with:

```python
groups = list(self.members.linked_groups(user_id))
if len(groups) >= 2:
    self._show_known_group_buttons(update_id, user_id, destination_chat_id, groups)
    return
if len(groups) == 1 and self.chats.tracked_count() == 1:
    self._verify_and_deliver(
        chat_id=groups[0][0],
        user_id=user_id,
        username=username,
        full_name=full_name,
        destination_chat_id=destination_chat_id,
    )
    return
self._show_picker(update_id, user_id, destination_chat_id)
```

Extract the existing opaque-token button construction into
`_show_known_group_buttons(...)`. Delete `_group_is_accessible`; concrete
membership remains checked only in `_verify_and_deliver` after selection.

- [ ] **Step 6: Render copy and run GREEN regressions**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python scripts/render_copy_deck.py
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/e2e/test_private_summary.py tests/e2e/test_reservation_recovery.py -q
```

Expected: selected tests PASS and `docs/BOT_COPY_RU.md` contains both keys.

- [ ] **Step 7: Commit Task 1**

```bash
git add content/bot_copy.ru.toml docs/BOT_COPY_RU.md app/services/on_demand.py \
  tests/e2e/test_private_summary.py tests/e2e/test_reservation_recovery.py
git commit -m "feat: distinguish private start and summary"
```

---

### Task 2: Acknowledge private requests before access checks

**Files:**
- Modify: `app/services/on_demand.py`
- Modify: `tests/e2e/test_on_demand_summary.py`
- Modify: `tests/e2e/test_private_summary.py`
- Modify: `tests/e2e/test_reservation_recovery.py`

**Interfaces:**
- Produces: `OnDemandSummaryService.acknowledge(destination_chat_id: int, reply_markup: dict | None = None) -> None`.
- Changes: `OnDemandSummaryService.request(..., send_ack: bool = True) -> None`.
- Preserves: group `/summary` sends exactly one acknowledgement through the default `send_ack=True`.

- [ ] **Step 1: Write RED event-order tests**

Make the private-flow Telegram fake record both send and membership events.
For a direct known group assert:

```python
assert telegram.events[:3] == [
    ("send_message", COPY.value("messages", "summary_accepted")),
    ("get_chat_member", -1001, BOT_USER_ID),
    ("get_chat_member", -1001, 42),
]
```

Add equivalent first-message assertions for deep-link and native
`chat_shared`. In `tests/e2e/test_on_demand_summary.py`, add a test where
`send_ack=False` emits only the final HTML summary.

- [ ] **Step 2: Write a RED durable-retry test**

In `tests/e2e/test_reservation_recovery.py`, make the first membership lookup
raise `TelegramApiError("temporary")` after the acknowledgement was sent.
Replay the same update and assert:

```python
assert [item[1] for item in raw.sent].count(
    COPY.value("messages", "summary_accepted")
) == 1
assert [item[1] for item in raw.sent].count("<b>done</b>") == 1
```

The fake effect repository must observe no payload-hash conflict.

- [ ] **Step 3: Run the RED tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/e2e/test_on_demand_summary.py tests/e2e/test_private_summary.py \
  tests/e2e/test_reservation_recovery.py -q
```

Expected: FAIL because acknowledgement currently occurs after membership
checks and `send_ack` does not exist.

- [ ] **Step 4: Separate acknowledgement from generation**

Add to `OnDemandSummaryService`:

```python
def acknowledge(self, destination_chat_id: int, reply_markup: dict | None = None) -> None:
    self.telegram.send_message(
        destination_chat_id,
        self.copy.value("messages", "summary_accepted"),
        reply_markup=reply_markup,
    )
```

Add keyword `send_ack: bool = True` to `request`. Call `acknowledge` only when
true. In `_verify_and_deliver`, acknowledge first, then check tracked status,
bot admin, and user membership; on success call:

```python
self.requests.request(
    chat_id=chat_id,
    requester_user_id=user_id,
    destination_chat_id=destination_chat_id,
    send_ack=False,
)
```

For native shared chats, move `is_tracked` into this post-ack path and keep the
exact `selected_group_not_connected` error.

- [ ] **Step 5: Run GREEN tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/e2e/test_on_demand_summary.py tests/e2e/test_private_summary.py \
  tests/e2e/test_reservation_recovery.py tests/unit/test_update_service.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/services/on_demand.py tests/e2e/test_on_demand_summary.py \
  tests/e2e/test_private_summary.py tests/e2e/test_reservation_recovery.py
git commit -m "fix: acknowledge private summaries immediately"
```

---

### Task 3: Replace Groq with a contract-tested DeepSeek client

**Files:**
- Create: `app/integrations/deepseek.py`
- Create: `tests/contract/test_deepseek_client.py`
- Create: `scripts/check_deepseek.py`
- Delete: `app/integrations/groq.py`
- Delete: `tests/contract/test_groq_client.py`
- Modify: `app/config.py`
- Modify: `app/web/production.py`
- Modify: `app/jobs/cli.py`
- Modify: `.env.example`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_cli_composition.py`
- Modify: `tests/unit/test_production_composition.py`
- Modify: `tests/unit/test_summary_service.py`

**Interfaces:**
- Produces: `DeepSeekClient(api_key: str, model: str = "deepseek-v4-flash", timeout_seconds: float = 90.0)`.
- Produces: `DeepSeekClient.generate_summary(system_prompt: str, user_prompt: str) -> LlmResult`.
- Produces: `DeepSeekApiError(message, status_code=None, retry_after_seconds=None)`.
- Changes Settings fields to `deepseek_api_key: str` and `deepseek_model: str`.

- [ ] **Step 1: Write the RED request-contract test**

Create a fake response returning:

```python
{
    "model": "deepseek-v4-flash-0731",
    "choices": [{"message": {"content": '{"headline":"Итог","topics":[]}'}}],
    "usage": {
        "prompt_tokens": 120,
        "prompt_cache_hit_tokens": 20,
        "prompt_cache_miss_tokens": 100,
        "completion_tokens": 30,
    },
}
```

Assert the request URL and exact body:

```python
assert captured["url"] == "https://api.deepseek.com/chat/completions"
assert captured["body"] == {
    "model": "deepseek-v4-flash",
    "messages": [
        {"role": "system", "content": "system JSON"},
        {"role": "user", "content": "user"},
    ],
    "thinking": {"type": "disabled"},
    "temperature": 0.2,
    "max_tokens": 1200,
    "response_format": {"type": "json_object"},
    "stream": False,
}
assert captured["timeout"] == 90
assert result.provider == "deepseek"
assert result.model == "deepseek-v4-flash-0731"
assert "secret" not in repr(captured["body"])
```

- [ ] **Step 2: Write RED error and keep-alive tests**

Parameterize `400, 401, 402, 422, 429, 500, 503`; assert status and
`Retry-After` are preserved without the key. Add a response body beginning
with blank lines before JSON and assert successful parsing.

- [ ] **Step 3: Write RED configuration/composition tests**

Replace all test environments with:

```python
"DEEPSEEK_API_KEY": "deepseek",
"DEEPSEEK_MODEL": "deepseek-v4-flash",
```

Assert `load_settings({})` reports `DEEPSEEK_API_KEY`, not `GROQ_API_KEY`, and
both webhook and CLI compositions construct `DeepSeekClient`.

- [ ] **Step 4: Run RED tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/contract/test_deepseek_client.py tests/unit/test_config.py \
  tests/unit/test_cli_composition.py tests/unit/test_production_composition.py -q
```

Expected: import/config failures because DeepSeek integration does not exist.

- [ ] **Step 5: Implement the standard-library DeepSeek client**

Mirror the proven Groq HTTP structure but use the exact Task 3 request body.
Strip leading/trailing whitespace before `json.loads`. Extract actual
`body["model"]`, falling back to configured model only when absent. Normalize
HTTP/network/malformed/empty-content errors as `DeepSeekApiError` and never
include the Authorization header or key in error text.

- [ ] **Step 6: Replace settings and both compositions**

Change `Settings` and `load_settings` to require `DEEPSEEK_API_KEY`, default
`DEEPSEEK_MODEL` to `deepseek-v4-flash`, and remove all reads/imports of Groq.
Update `.env.example` accordingly. Create `scripts/check_deepseek.py` that loads
settings, performs a minimal JSON request containing no group text, validates
the returned object, and prints only provider/model/token counts.

- [ ] **Step 7: Run GREEN and obsolete-provider checks**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/contract/test_deepseek_client.py tests/unit/test_config.py \
  tests/unit/test_cli_composition.py tests/unit/test_production_composition.py \
  tests/unit/test_summary_service.py -q
if rg -n "GroqClient|GroqApiError|GROQ_API_KEY|GROQ_MODEL|integrations\.groq" \
  app scripts tests .env.example; then exit 1; fi
```

Expected: selected tests PASS and obsolete-provider scan has no matches.

- [ ] **Step 8: Commit Task 3**

```bash
git add app/integrations app/config.py app/web/production.py app/jobs/cli.py \
  scripts/check_deepseek.py .env.example tests
git commit -m "feat: switch summaries to DeepSeek"
```

---

### Task 4: Persist DeepSeek usage and exact micro-USD estimates

**Files:**
- Create: `app/services/llm_usage.py`
- Create: `migrations/003_deepseek_usage.sql`
- Create: `tests/unit/test_llm_usage.py`
- Modify: `app/domain/models.py`
- Modify: `app/integrations/deepseek.py`
- Modify: `app/services/summaries.py`
- Modify: `app/repositories/mysql.py`
- Modify: `tests/contract/test_deepseek_client.py`
- Modify: `tests/unit/test_summary_service.py`
- Modify: `tests/integration/test_migrations.py`
- Modify: `tests/integration/test_mysql_repositories.py`

**Interfaces:**
- Produces: `estimate_flash_cost_microusd(hit: int, miss: int, output: int, requested_at: datetime) -> int`.
- Extends `LlmResult` with `cache_hit_input_tokens`, `cache_miss_input_tokens`, and `estimated_cost_microusd`.
- Extends `GeneratedSummary` with latency/token/character/call/cost totals.
- Adds four nullable/zero-safe columns to `prepared_summaries`.

- [ ] **Step 1: Write RED pricing-boundary tests**

In `tests/unit/test_llm_usage.py`, cover Monday UTC boundaries
`00:59:59`, `01:00`, `03:59:59`, `04:00`, `05:59:59`, `06:00`, `09:59:59`,
`10:00`, plus a Sunday. For 1,000 tokens in each bucket assert off-peak uses
rates `0.007/0.22/0.66` micro-USD per token and peak uses
`0.014/0.44/1.32`, rounded once with `Decimal` and `ROUND_HALF_UP`.

Example:

```python
assert estimate_flash_cost_microusd(0, 1000, 0, monday_00_utc) == 220
assert estimate_flash_cost_microusd(0, 1000, 0, monday_01_utc) == 440
assert estimate_flash_cost_microusd(1000, 0, 0, monday_00_utc) == 7
```

- [ ] **Step 2: Write RED aggregation and repository tests**

Make the fake LLM return an invalid first `LlmResult` and valid repair result
with distinct metrics. Assert saved `GeneratedSummary` contains sums for
latency, input, output, hit, miss, `llm_call_count == 2`, prompt character
count, and micro-USD. Extend the MySQL recording test to assert every field is
present in INSERT and `ON DUPLICATE KEY UPDATE`.

- [ ] **Step 3: Write the additive migration RED test**

Require migration files `001`, `002`, `003`. Assert `003` contains:

```sql
ALTER TABLE prepared_summaries ADD COLUMN cache_hit_input_tokens INT NULL;
ALTER TABLE prepared_summaries ADD COLUMN cache_miss_input_tokens INT NULL;
ALTER TABLE prepared_summaries ADD COLUMN llm_call_count INT NULL;
ALTER TABLE prepared_summaries ADD COLUMN estimated_cost_microusd BIGINT NULL;
```

Add interruption/restart parameterization for every statement boundary and
extend migration verification so the checksum is recorded only after all new
columns exist.

- [ ] **Step 4: Run RED tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/unit/test_llm_usage.py tests/unit/test_summary_service.py \
  tests/integration/test_migrations.py tests/integration/test_mysql_repositories.py -q
```

Expected: missing calculator/model fields/migration assertions fail.

- [ ] **Step 5: Implement pricing and DeepSeek usage parsing**

Use `Decimal` constants and UTC half-open peak ranges:

```python
PEAK_RANGES = ((time(1), time(4)), (time(6), time(10)))
OFF_PEAK = (Decimal("0.007"), Decimal("0.22"), Decimal("0.66"))
PEAK = (Decimal("0.014"), Decimal("0.44"), Decimal("1.32"))
```

Parse `prompt_tokens`, `prompt_cache_hit_tokens`,
`prompt_cache_miss_tokens`, and `completion_tokens`. If miss is absent, derive
`max(prompt_tokens - hit, 0)`. The client captures request-start UTC time and
returns its per-call estimate.

- [ ] **Step 6: Aggregate every successful initial/repair call**

In `SummaryService.generate`, keep `llm_results: list[LlmResult]` and append
immediately after each successful API call, before JSON validation. Set
provider/model from the last successful call even when final output falls back
to heuristic. Sum all fields once when constructing `GeneratedSummary`; use
`len(user_prompt)` for `input_characters` and `len(llm_results)` for calls.

- [ ] **Step 7: Implement migration and MySQL persistence**

Create `003_deepseek_usage.sql`, extend restart-safe duplicate-DDL handling and
schema verification, and include all old/new metric fields in both INSERT and
upsert update. Old rows stay NULL; no UPDATE over historical rows is allowed.

- [ ] **Step 8: Run GREEN tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/contract/test_deepseek_client.py tests/unit/test_llm_usage.py \
  tests/unit/test_summary_service.py tests/integration/test_migrations.py \
  tests/integration/test_mysql_repositories.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 9: Commit Task 4**

```bash
git add app/domain/models.py app/integrations/deepseek.py app/services/llm_usage.py \
  app/services/summaries.py app/repositories/mysql.py migrations/003_deepseek_usage.sql \
  tests/contract/test_deepseek_client.py tests/unit/test_llm_usage.py \
  tests/unit/test_summary_service.py tests/integration
git commit -m "feat: record DeepSeek usage costs"
```

---

### Task 5: Add live balance, usage CLI, and idempotent weekly report

**Files:**
- Create: `app/services/usage_reports.py`
- Create: `tests/unit/test_usage_reports.py`
- Modify: `app/domain/models.py`
- Modify: `app/domain/ports.py`
- Modify: `app/integrations/deepseek.py`
- Modify: `app/repositories/mysql.py`
- Modify: `app/jobs/cli.py`
- Modify: `migrations/003_deepseek_usage.sql`
- Modify: `tests/contract/test_deepseek_client.py`
- Modify: `tests/integration/test_migrations.py`
- Modify: `tests/integration/test_mysql_repositories.py`
- Modify: `tests/unit/test_cli_composition.py`

**Interfaces:**
- Produces: `DeepSeekClient.get_balance() -> tuple[bool, tuple[LlmBalance, ...]]`.
- Produces: `MySqlUsageRepository.aggregate(provider, start, end) -> UsageTotals`.
- Produces: `MySqlUsageRepository.previous_snapshot(provider, currency, week_start) -> BalanceSnapshot | None`.
- Produces: `MySqlUsageRepository.get_or_create_snapshot(..., report_text) -> BalanceSnapshot`.
- Produces: `UsageReportService.inspect(now) -> dict` and `prepare_weekly(now) -> FrozenUsageReport`.
- Adds CLI commands `usage` and `usage-report`.

- [ ] **Step 1: Write RED balance contract tests**

Mock GET `/user/balance` with:

```python
{
    "is_available": True,
    "balance_infos": [{
        "currency": "CNY",
        "total_balance": "50.000000",
        "granted_balance": "0.000000",
        "topped_up_balance": "50.000000",
    }],
}
```

Assert method `GET`, Bearer auth, `Decimal` values, and no key leakage on HTTP
or malformed-body errors.

- [ ] **Step 2: Write RED usage-window/report tests**

At Sunday `2026-09-06 18:30 UTC`, assert week start is Monday Moscow midnight,
month start is September 1 Moscow midnight, report contains calls/repair/hit/
miss/output/USD/CNY, and first-run text says `прогноз появится через неделю`.
With previous CNY 50 and current 49, assert delta `−1.00 CNY` and forecast
`примерно 49 недель`. With current 52, assert a top-up and omit runway.

- [ ] **Step 3: Write RED repository/migration tests**

Extend migration 003 with `llm_balance_snapshots` including `report_text TEXT`
and unique `(provider, currency, report_week_start)`. Repository tests must
assert:

```sql
SUM(llm_call_count)
SUM(cache_hit_input_tokens)
SUM(cache_miss_input_tokens)
SUM(estimated_cost_microusd)
```

and prove `get_or_create_snapshot` returns the frozen existing `report_text`
instead of overwriting it on a retry.

- [ ] **Step 4: Write RED CLI/durable-effect tests**

Require `COMMANDS` to contain `usage` and `usage-report`. `usage` prints data
without Telegram. `usage-report` without `OWNER_CHAT_ID` raises a clear
configuration error before send. Two invocations for the same week must use:

```python
weekly_update_id = -int(week_start.strftime("%Y%m%d"))
```

inside `telegram_update_scope`, yielding one durable Telegram message.

- [ ] **Step 5: Run RED tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/contract/test_deepseek_client.py tests/unit/test_usage_reports.py \
  tests/unit/test_cli_composition.py tests/integration/test_migrations.py \
  tests/integration/test_mysql_repositories.py -q
```

Expected: missing balance/report/repository/CLI behavior fails.

- [ ] **Step 6: Implement balance parsing and usage models**

Add frozen dataclasses `LlmBalance`, `UsageTotals`, `BalanceSnapshot`, and
`FrozenUsageReport`; all monetary fields use `Decimal` or integer micro-USD.
Implement `get_balance` separately from summary generation so its failure never
changes summary behavior.

- [ ] **Step 7: Implement repository queries and frozen snapshots**

Aggregate only `provider="deepseek"` rows within UTC boundaries derived by the
service. Use `COALESCE` for nullable historic metrics. Snapshot insertion uses
`INSERT IGNORE` followed by SELECT; never update `report_text` for an existing
week/currency.

- [ ] **Step 8: Implement usage service and CLI commands**

`inspect` always returns local week/month totals and attempts live balance;
balance errors become `balance_available=False`. `prepare_weekly` freezes one
report per week. `usage-report` builds `DurableTelegramGateway`, enters the
stable negative update scope, sends to `OWNER_CHAT_ID`, and returns JSON status.

- [ ] **Step 9: Run GREEN and idempotency tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/contract/test_deepseek_client.py tests/unit/test_usage_reports.py \
  tests/unit/test_cli_composition.py tests/integration/test_migrations.py \
  tests/integration/test_mysql_repositories.py tests/unit/test_telegram_effects.py -q
```

Expected: all selected tests PASS and duplicate weekly execution sends once.

- [ ] **Step 10: Commit Task 5**

```bash
git add app/domain app/integrations/deepseek.py app/repositories/mysql.py \
  app/services/usage_reports.py app/jobs/cli.py migrations/003_deepseek_usage.sql \
  tests
git commit -m "feat: report weekly DeepSeek spend"
```

---

### Task 6: Document, package, verify, and deploy release 0.3.0

**Files:**
- Modify: `app/__init__.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/PRODUCTION_CUTOVER.md`
- Modify: `scripts/deploy_beget.exp`
- Modify: `tests/unit/test_version.py`
- Modify: `tests/unit/test_deploy_safety.py`
- Verify: `content/bot_copy.ru.toml`
- Verify: `docs/BOT_COPY_RU.md`

**Interfaces:**
- Produces application/package version `0.3.0`.
- Production cron target: Sunday `21:30 Europe/Moscow`.
- Deploy precondition: remote `.env` contains valid `DEEPSEEK_API_KEY` and the safe smoke succeeds before Passenger restart.

- [ ] **Step 1: Write RED release/deploy tests**

Set version assertion to `0.3.0`. Extend deploy-safety assertions so the remote
install command order is:

```text
backup -> pip install -> migrate -> scripts/check_deepseek.py -> touch restart.txt
```

Assert the smoke command occurs before restart and deployment still excludes
`.env` and every virtualenv.

- [ ] **Step 2: Run RED tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/unit/test_version.py tests/unit/test_deploy_safety.py -q
```

Expected: version and missing pre-restart DeepSeek smoke fail.

- [ ] **Step 3: Update release metadata, deploy guard, and docs**

Set package/app/README to `0.3.0`. Add changelog entries for private flow,
DeepSeek, usage persistence, and weekly reporting. Replace Groq setup text with
DeepSeek key/model, document external processing in the PRC, add:

```text
30 21 * * 0 cd /home/p/pshech/brevity.prosto-kosmos.ru && \
  .venv/bin/python -m app.jobs.cli usage-report >> logs/usage-report-$(date -I).log 2>&1
```

and note that Beget schedule must resolve to Sunday 21:30 Moscow. Add
`.venv/bin/python scripts/check_deepseek.py` to deploy before restart.

- [ ] **Step 4: Run the full local release gate**

Run exactly:

```bash
set -e
PY=/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python
"$PY" -m pytest -q
"$PY" -m ruff check app scripts tests passenger_wsgi.py public_html/passenger_wsgi.py
"$PY" -m compileall -q app scripts public_html passenger_wsgi.py
"$PY" -m pip check
"$PY" scripts/render_copy_deck.py --check
git diff --check
if rg -n "Groq|GROQ_|qwen/qwen3\.6-27b|integrations\.groq" \
  app scripts tests README.md docs/PRODUCTION_CUTOVER.md docs/BOT_COPY_RU.md \
  .env.example pyproject.toml; then exit 1; fi
if git diff --name-only abb587a..HEAD -- mvp-1 | rg .; then exit 1; fi
```

Expected: all tests PASS; every auxiliary check is clean; no Groq or `mvp-1`
change remains.

- [ ] **Step 5: Commit Task 6**

```bash
git add app/__init__.py pyproject.toml README.md CHANGELOG.md \
  docs/PRODUCTION_CUTOVER.md scripts/deploy_beget.exp \
  tests/unit/test_version.py tests/unit/test_deploy_safety.py
git commit -m "chore: release 0.3.0"
```

- [ ] **Step 6: Independent final review**

Generate a review package from `2efcb15` to HEAD. Resolve every Critical and
Important finding in one fix wave, rerun covering tests, re-review, then rerun
the full release gate from Step 4.

- [ ] **Step 7: Configure secret and perform pre-deploy DeepSeek smoke**

Ask the user to place only `DEEPSEEK_API_KEY` and optional
`DEEPSEEK_MODEL=deepseek-v4-flash` in the existing remote `.env`. Verify the
key by running `scripts/check_deepseek.py`; output must contain provider/model/
usage only and no group content or key.

- [ ] **Step 8: Deploy and verify production**

Run `scripts/deploy_beget.exp`; require backup path, migration 003 applied,
DeepSeek smoke success, and Passenger restart. Then verify:

```text
GET /health -> HTTP 200
remote app.__version__ -> 0.3.0
remote audit -> healthy=true and queued_updates=0
remote usage -> valid local totals and live CNY balance
plain /start -> welcome without generation
/summary with ambiguous groups -> picker
selected group -> acknowledgement before final summary
prepared summary -> provider deepseek with non-null usage/cost
```

- [ ] **Step 9: Configure and prove the weekly cron**

Add the Sunday usage-report job in Beget, run it once manually, verify one
Telegram report and one frozen snapshot/effect, then rerun the same command and
prove no duplicate message is sent.

- [ ] **Step 10: Push and verify GitHub**

```bash
git push origin codex/private-summary-implementation
git fetch origin codex/private-summary-implementation
git rev-list --left-right --count \
  origin/codex/private-summary-implementation...HEAD
git status --short
```

Expected: `0 0` and clean status.
