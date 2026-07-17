# BrevitySisterBot 0.2.2 Private Summary Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make private `/summary` choose the correct group, give plain `/start` a distinct welcome action, and send the two-minute acknowledgement before slow Telegram checks and summary generation.

**Architecture:** Keep the synchronous webhook and durable Telegram-effect model. Use database-known group links when they are complete; when one known group cannot prove uniqueness because several groups are connected globally, use Telegram's native `request_chat` picker instead of enumerating memberships through external API calls. Separate acknowledgement from generation so a selected group is acknowledged before access checks while group `/summary` still emits exactly one acknowledgement.

**Tech Stack:** Python 3.10, WSGI/Passenger, PyMySQL/MySQL, Telegram Bot API, pytest, Ruff, TOML copy catalog.

## Global Constraints

- Release version is exactly `0.2.2`.
- Plain `/start` sends a welcome message and an inline «Получить сводку» button; it does not generate a summary.
- `/start summary_<token>` remains a direct request for the token's group.
- A single known group is auto-selected only when the total tracked-group count is exactly one.
- Multiple known groups are shown immediately without pre-checking every membership through Telegram API.
- If zero groups are known, or one group is known while multiple groups are tracked, use Telegram's native group picker.
- Send `summary_accepted` only after a group is selected, but before `getChatMember` and before model generation.
- Do not add an artificial delay between acknowledgement and a cached summary.
- `/enable_daily` immediately remembers the administrator who connected the group.
- All user-facing copy remains in `content/bot_copy.ru.toml` as the single source of truth.
- Keep durable Telegram-effect idempotency and the five-minute recovery fallback unchanged.
- Do not modify `mvp-1/`.
- No database schema migration is required.

---

### Task 1: Track the enabling administrator and expose tracked-group count

**Files:**
- Modify: `app/domain/ports.py`
- Modify: `app/repositories/mysql.py`
- Modify: `app/services/group_updates.py`
- Modify: `tests/integration/test_mysql_repositories.py`
- Modify: `tests/e2e/test_group_commands.py`

**Interfaces:**
- Produces: `ChatRepository.tracked_count() -> int`.
- Produces: successful `/enable_daily` calls `MemberRepository.remember_group(chat_id, actor_id, username, full_name, now)` before publishing onboarding copy.
- Consumes: existing `tracked_chats` rows and `MemberRepository.remember_group`.

- [ ] **Step 1: Write failing repository and group-command tests**

Add an integration assertion using `RecordingConnection(fetchone_result={"tracked_count": 2})`:

```python
def test_chat_repository_counts_tracked_groups_regardless_of_daily_setting() -> None:
    connection = RecordingConnection(fetchone_result={"tracked_count": 2})
    repository = MySqlTrackedChatRepository(lambda: connection)

    assert repository.tracked_count() == 2
    sql, params = connection.cursor_instance.executions[0]
    assert "COUNT(*) AS tracked_count" in sql
    assert "WHERE enabled" not in sql
    assert params is None
    assert connection.closed is True
```

Extend `test_enable_daily_requires_admin_and_tracks_group` so the successful
call also asserts:

```python
assert members.remembered == [(-1001, 42)]
```

The denied call must leave `members.remembered == []`.

- [ ] **Step 2: Run RED tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/integration/test_mysql_repositories.py::test_chat_repository_counts_tracked_groups_regardless_of_daily_setting \
  tests/e2e/test_group_commands.py::test_enable_daily_requires_admin_and_tracks_group -q
```

Expected: FAIL because `tracked_count` does not exist and `/enable_daily` does
not remember the actor.

- [ ] **Step 3: Implement the port and MySQL query**

Add to `ChatRepository`:

```python
def tracked_count(self) -> int:
    raise NotImplementedError
```

Implement in `MySqlTrackedChatRepository`:

```python
def tracked_count(self) -> int:
    with _managed_connection(self.connection_factory) as connection:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS tracked_count FROM tracked_chats"
        )
        row = cursor.fetchone()
        return int(row["tracked_count"]) if row is not None else 0
```

Add compatible `tracked_count` methods to test fakes that implement the full
chat port.

- [ ] **Step 4: Remember the successful enabling actor**

In the successful `/enable_daily` branch, immediately after
`access.enable_daily(...)` returns, add:

```python
self.members.remember_group(
    message.chat_id,
    message.user_id,
    message.username,
    message.full_name,
    current,
)
```

Do not call it on either admin-denied path.

- [ ] **Step 5: Run GREEN tests and focused regressions**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/integration/test_mysql_repositories.py \
  tests/e2e/test_group_commands.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/domain/ports.py app/repositories/mysql.py app/services/group_updates.py \
  tests/integration/test_mysql_repositories.py tests/e2e/test_group_commands.py
git commit -m "fix: remember newly connected group"
```

---

### Task 2: Give plain `/start` a welcome action and make group choice complete

**Files:**
- Modify: `content/bot_copy.ru.toml`
- Modify: `docs/BOT_COPY_RU.md`
- Modify: `app/services/on_demand.py`
- Modify: `tests/e2e/test_private_summary.py`
- Modify: `tests/e2e/test_reservation_recovery.py`
- Modify: any chat-repository fake that needs `tracked_count()`

**Interfaces:**
- Consumes: `ChatRepository.tracked_count() -> int` from Task 1.
- Produces: callback data `summary_start` for the welcome button.
- Produces: `_handle_private_summary(...)` as the shared entry for `/summary` and `summary_start`.
- Copy keys: `messages.private_welcome` and `buttons.start_summary`.

- [ ] **Step 1: Add RED tests for plain `/start` and its button**

Add a test that calls `handler(private_message("/start"))` and asserts:

```python
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

Add a callback fixture with `data="summary_start"`. Assert that the callback is
answered and follows the same picker path as `/summary`.

- [ ] **Step 2: Add RED tests for complete group choice**

Extend `FakeChats` with an explicit `tracked_group_count` value and
`tracked_count()` method. Add these cases:

```python
def test_one_known_group_is_not_auto_selected_when_other_groups_are_enabled():
    # members.groups has one item, chats.tracked_group_count == 2
    # Expect choose_group_native and no summaries.generate call.

def test_one_known_group_is_auto_selected_when_it_is_the_only_enabled_group():
    # members.groups has one item, chats.tracked_group_count == 1
    # Expect the direct-delivery path.

def test_multiple_known_groups_are_shown_without_membership_api_calls():
    # members.groups has two items, chats.tracked_group_count == 2
    # Expect inline group buttons and telegram.get_chat_member call count == 0.
```

Record `FakeTelegram.membership_calls` in `get_chat_member` so the third test
asserts an empty list.

- [ ] **Step 3: Run RED tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/e2e/test_private_summary.py -q
```

Expected: FAIL for missing copy keys, missing callback route, and the old
single-known-group auto-selection.

- [ ] **Step 4: Add copy and plain `/start` routing**

Add TOML values with stable wording:

```toml
private_welcome = "Привет! Я готовлю краткие сводки по сообщениям из подключённых групп. Нажмите кнопку ниже или отправьте /summary."
```

```toml
start_summary = "Получить сводку"
```

For a plain `/start`, send `private_welcome` with one inline button carrying
`callback_data = "summary_start"`; return without generation. Keep the existing
deep-link branch before the plain-start branch so `/start summary_<token>` keeps
its direct-group meaning.

In `_handle_callback`, route `summary_start` first:

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

- [ ] **Step 5: Implement the complete-choice decision table**

Change `_handle_private_summary` to avoid membership calls before a picker:

```python
groups = list(self.members.linked_groups(user_id))
if len(groups) >= 2:
    self._show_known_group_buttons(...)
    return
if len(groups) == 1 and self.chats.tracked_count() == 1:
    self._verify_and_deliver(...)
    return
self._show_picker(update_id, user_id, destination_chat_id)
```

Extract the current token/button construction into a focused helper such as
`_show_known_group_buttons(...)`. Do not call `_group_is_accessible` while
building the picker. Access remains rechecked after a concrete group is chosen.

- [ ] **Step 6: Render copy documentation and run GREEN tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python scripts/render_copy_deck.py
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/e2e/test_private_summary.py tests/e2e/test_reservation_recovery.py -q
```

Expected: all selected tests PASS and `docs/BOT_COPY_RU.md` contains both new
keys.

- [ ] **Step 7: Commit Task 2**

```bash
git add content/bot_copy.ru.toml docs/BOT_COPY_RU.md app/services/on_demand.py \
  tests/e2e/test_private_summary.py tests/e2e/test_reservation_recovery.py
git commit -m "feat: distinguish private start and summary"
```

---

### Task 3: Send acknowledgement before access checks and generation

**Files:**
- Modify: `app/services/on_demand.py`
- Modify: `tests/e2e/test_on_demand_summary.py`
- Modify: `tests/e2e/test_private_summary.py`
- Modify: `tests/e2e/test_reservation_recovery.py`

**Interfaces:**
- Produces: `OnDemandSummaryService.acknowledge(destination_chat_id: int, reply_markup: dict | None = None) -> None`.
- Changes: `OnDemandSummaryService.request(..., send_ack: bool = True) -> None`.
- Consumes: existing durable `TelegramGateway.send_message` sequencing.

- [ ] **Step 1: Write RED order tests**

Make `FakeTelegram` record both `send_message` and `get_chat_member` events.
For a direct private summary, assert the first three events are:

```python
assert telegram.events[:3] == [
    ("send_message", COPY.value("messages", "summary_accepted")),
    ("get_chat_member", -1001, BOT_USER_ID),
    ("get_chat_member", -1001, 42),
]
```

Assert `summaries.generate` happens only after both membership checks. Add the
same visible-ack assertion for deep-link and native `chat_shared` delivery.

Add a service test proving `send_ack=False` emits only the generated summary,
while the default still emits acknowledgement plus result for group
`/summary`.

- [ ] **Step 2: Run RED tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/e2e/test_on_demand_summary.py \
  tests/e2e/test_private_summary.py \
  tests/e2e/test_reservation_recovery.py -q
```

Expected: FAIL because private acknowledgement currently occurs after
membership checks and `send_ack` does not exist.

- [ ] **Step 3: Separate acknowledgement from generation**

Add:

```python
def acknowledge(
    self,
    destination_chat_id: int,
    reply_markup: dict | None = None,
) -> None:
    self.telegram.send_message(
        destination_chat_id,
        self.copy.value("messages", "summary_accepted"),
        reply_markup=reply_markup,
    )
```

Extend `request` with `send_ack: bool = True` and replace the unconditional
send with:

```python
if send_ack:
    self.acknowledge(destination_chat_id, ack_reply_markup)
```

- [ ] **Step 4: Acknowledge private requests before verification**

At the start of `_verify_and_deliver`, call:

```python
self.requests.acknowledge(
    destination_chat_id,
    {"remove_keyboard": True} if remove_keyboard else None,
)
```

Then perform tracked-group, bot-admin, and user-membership checks. On success,
call `self.requests.request(..., send_ack=False)` so the accepted message is not
duplicated. Move the `is_tracked` check for native shared chats into this
post-ack verification path while retaining `selected_group_not_connected`.

- [ ] **Step 5: Verify durable retry ordering**

Extend the recovery test so the first attempt sends the acknowledgement and
then fails during a membership API call. Replaying the same update must retain
one sent acknowledgement effect, continue with stable sequence numbers, and
eventually emit one summary. There must be no payload-hash mismatch.

- [ ] **Step 6: Run GREEN and focused regression tests**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/e2e/test_on_demand_summary.py \
  tests/e2e/test_private_summary.py \
  tests/e2e/test_reservation_recovery.py \
  tests/unit/test_update_service.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add app/services/on_demand.py tests/e2e/test_on_demand_summary.py \
  tests/e2e/test_private_summary.py tests/e2e/test_reservation_recovery.py
git commit -m "fix: acknowledge private summaries immediately"
```

---

### Task 4: Package and verify release 0.2.2

**Files:**
- Modify: `app/__init__.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/unit/test_version.py`
- Verify: `content/bot_copy.ru.toml`
- Verify: `docs/BOT_COPY_RU.md`

**Interfaces:**
- Produces: application/package version `0.2.2`.
- Consumes: all behavior from Tasks 1–3.

- [ ] **Step 1: Write the RED version assertion**

Change the version test to:

```python
def test_application_version_is_0_2_2() -> None:
    assert __version__ == "0.2.2"
```

- [ ] **Step 2: Run the version test RED**

Run:

```bash
/Users/pavel/projects/BrevitySisterBot/.venv-0.2/bin/python -m pytest \
  tests/unit/test_version.py -q
```

Expected: FAIL with `0.2.1 != 0.2.2`.

- [ ] **Step 3: Bump release metadata and changelog**

Set `app.__version__`, `pyproject.toml`, and the README heading to `0.2.2`.
Add a dated `0.2.2 — 2026-07-17` changelog entry covering:

```text
- выбор группы больше не подменяется единственной известной привязкой;
- обычный /start получил приветствие и кнопку;
- подтверждение личного запроса отправляется до проверок и генерации;
- /enable_daily сразу запоминает подключившего группу администратора.
```

- [ ] **Step 4: Run the full release gate**

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
if rg -n "Сначала запросите /summary внутри|📝 Сводка за сегодня|1–2 минут|буферизация" \
  app content tests docs/BOT_COPY_RU.md; then exit 1; fi
```

Expected: all tests PASS; Ruff, compileall, pip check, renderer, diff check,
and obsolete-copy scan are clean.

- [ ] **Step 5: Commit Task 4**

```bash
git add app/__init__.py pyproject.toml README.md CHANGELOG.md \
  tests/unit/test_version.py
git commit -m "chore: release 0.2.2"
```

- [ ] **Step 6: Independent review, Beget deploy, and GitHub push**

Generate a review package from the pre-Task-1 base to HEAD. Resolve all
Critical and Important findings, rerun the full gate, deploy with
`scripts/deploy_beget.exp`, and verify:

```text
public /health returns HTTP 200
remote app.__version__ is 0.2.2
remote audit reports healthy=true and queued_updates=0
plain /start sends welcome without generation
/summary with one remembered group and two connected groups opens a picker
selected group sends summary_accepted before the result
```

Push `codex/private-summary-implementation` to
`git@github-brevitysisterbot:dalie86/BrevitySisterBot.git`, fetch it back, and
require `git rev-list --left-right --count origin/codex/private-summary-implementation...HEAD`
to output `0 0`.
