# Private Summary Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Убрать обязательный групповой `/summary`, запоминать доступные пользователю группы, выдавать личную сводку через deep link или нативный Telegram picker и перенести весь вординг в единый TOML-файл.

**Architecture:** На входе Telegram расширяется типизированными событиями `chat_shared` и `chat_member`; сервисы проверяют текущий доступ через `getChatMember`, а MySQL хранит непрозрачные токены и одноразовые запросы выбора. `content/bot_copy.ru.toml` загружается один раз при старте, валидируется и передаётся сервисам; итоговая сводка формируется как безопасный Telegram HTML с экранированными динамическими данными.

**Tech Stack:** Python 3.11+, WSGI, Telegram Bot API, MySQL/InnoDB, PyMySQL, `tomllib`, pytest, Ruff, Groq API.

## Global Constraints

- Версия приложения остаётся `0.2.0`.
- Развёртывание рассчитано на обычный хостинг Beget и каталог `brevity.prosto-kosmos.ru/public_html`, без VPS и постоянно работающего worker-процесса.
- Автоматическая публикация остаётся в 21:00 по Москве; ручной порог — 5 сообщений, автоматический — 15.
- `/disable_daily` выключает только автоматическую публикацию; накопление сообщений, ручные и личные сводки продолжают работать.
- BrevitySisterBot должен быть администратором подключённой группы; членство пользователя проверяется перед каждой личной выдачей.
- Сообщение «Получить сводку лично» публикуется после `/enable_daily`, но не закрепляется автоматически.
- Deep link содержит только случайный 128-битный токен, не `chat_id`; нативный picker использует одноразовый signed 32-bit `request_id` со сроком один час.
- Повторная обработка Telegram update не должна дублировать сообщения благодаря существующему durable effect ledger.
- Единственный runtime-источник пользовательских сообщений, кнопок, шаблонов и LLM-промптов — `content/bot_copy.ru.toml`.
- Заголовок основной и резервной сводки: жирный `📝 Краткая сводка за сегодня`; динамический HTML всегда экранируется.
- Итоговая сводка не превышает 3900 символов; LLM получает не более 18 000 символов переписки, не более четырёх тем и одну попытку починки ответа размером до 6000 символов.
- Миграция `001_initial.sql` не изменяется; новая схема добавляется только через `002_private_summary_onboarding.sql`.
- Каталог `mvp-1/` остаётся историческим снимком 0.1.0 и в этой работе не изменяется.

---

## File map

- Create `app/content.py`: загрузка, строгая проверка и подстановка `{{name}}` в TOML-каталоге.
- Modify `content/bot_copy.ru.toml`: утверждённый runtime-каталог.
- Create `scripts/render_copy_deck.py`: синхронизация редакторского Markdown с TOML.
- Modify `app/services/summaries.py`: промпты, основной и fallback-шаблоны из каталога, HTML-экранирование.
- Modify `app/domain/models.py`: типизированное состояние членства Telegram.
- Modify `app/domain/ports.py`: расширенные репозитории и Telegram gateway.
- Modify `app/integrations/telegram.py`: `parse_mode`, полные данные `getChatMember`, `chat_shared`, `chat_member`.
- Create `migrations/002_private_summary_onboarding.sql`: токены групп и запросы нативного picker.
- Modify `app/repositories/mysql.py`: токены, одноразовые запросы и состояние членства.
- Modify `app/services/access.py`: проверка администратора-инициатора и самого бота без пользовательского текста в Python.
- Modify `app/services/group_updates.py`: onboarding-сообщение и пассивное запоминание группы.
- Modify `app/services/on_demand.py`: deep link, picker, фильтрация групп и личная выдача.
- Create `app/services/membership_updates.py`: реакция на `chat_member` для уже известных участников.
- Modify `app/services/telegram_effects.py`: идемпотентная отправка с `parse_mode`.
- Modify `app/services/lifecycle.py`: HTML-режим плановых сводок.
- Modify `app/config.py`, `app/web/production.py`, `app/jobs/cli.py`: composition root и `TELEGRAM_BOT_USERNAME`.
- Modify `scripts/set_webhook.py`: подписка на `chat_member`.
- Modify `README.md`, `hosting.env.example`, `docs/BOT_COPY_RU.md`: эксплуатационный контракт.

---

### Task 1: Runtime copy catalog

**Files:**
- Create: `app/content.py`
- Modify: `content/bot_copy.ru.toml`
- Test: `tests/unit/test_content.py`

**Interfaces:**
- Consumes: `content/bot_copy.ru.toml` с секциями `messages`, `buttons`, `summary`, `fallback`, `prompts`.
- Produces: `BotCopy.load(path: Path) -> BotCopy`, `BotCopy.value(section: str, key: str) -> str`, `BotCopy.render(section: str, key: str, **values: object) -> str`.

- [ ] **Step 1: Write failing catalog tests**

```python
from pathlib import Path

import pytest

from app.content import BotCopy, CopyConfigurationError


def test_repository_copy_catalog_loads_and_renders() -> None:
    copy = BotCopy.load(Path("content/bot_copy.ru.toml"))

    assert copy.value("buttons", "get_private_summary") == "Получить сводку лично"
    assert copy.render(
        "summary",
        "topic",
        topic_title="Релиз",
        topic_details="Версия согласована.",
    ) == "• Релиз\nВерсия согласована."


def test_catalog_rejects_missing_required_placeholder(tmp_path: Path) -> None:
    source = Path("content/bot_copy.ru.toml").read_text(encoding="utf-8")
    broken = tmp_path / "copy.toml"
    broken.write_text(source.replace("{{headline}}", "итог", 1), encoding="utf-8")

    with pytest.raises(CopyConfigurationError, match="summary.document.*headline"):
        BotCopy.load(broken)


def test_render_rejects_unknown_or_missing_values() -> None:
    copy = BotCopy.load(Path("content/bot_copy.ru.toml"))

    with pytest.raises(CopyConfigurationError, match="topic_details"):
        copy.render("summary", "topic", topic_title="Релиз")
    with pytest.raises(CopyConfigurationError, match="unexpected"):
        copy.render(
            "summary",
            "topic",
            topic_title="Релиз",
            topic_details="Готово",
            unexpected="value",
        )
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_content.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'app.content'`.

- [ ] **Step 3: Implement strict loading and explicit placeholder replacement**

```python
# app/content.py
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
REQUIRED: dict[tuple[str, str], frozenset[str]] = {
    ("summary", "document"): frozenset({"headline", "topics_block"}),
    ("summary", "topic"): frozenset({"topic_title", "topic_details"}),
    ("fallback", "document"): frozenset(
        {"message_count", "participants", "recent_messages"}
    ),
    ("fallback", "message_item"): frozenset({"message_text"}),
    ("prompts", "user"): frozenset({"transcript"}),
    ("prompts", "transcript_line"): frozenset(
        {"iso_datetime", "participant_name", "message_text"}
    ),
    ("prompts", "repair"): frozenset({"invalid_model_response"}),
}
STATIC_KEYS = {
    ("messages", key)
    for key in {
        "group_commands", "daily_enabled", "daily_disabled", "group_not_connected",
        "admin_required", "summary_accepted", "summary_in_progress",
        "not_enough_messages", "choose_group_native", "choose_group_known",
        "group_choice_expired", "membership_not_confirmed", "bot_admin_required",
        "private_link_invalid", "selected_group_not_connected",
        "group_access_unverifiable",
    }
} | {
    ("buttons", "get_private_summary"),
    ("buttons", "choose_group"),
    ("summary", "topics_separator"),
    ("fallback", "participants_unknown"),
    ("fallback", "messages_separator"),
    ("prompts", "system"),
}


class CopyConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BotCopy:
    sections: Mapping[str, Mapping[str, str]]

    @classmethod
    def load(cls, path: Path) -> "BotCopy":
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise CopyConfigurationError(f"Cannot load bot copy: {exc}") from exc
        sections = {
            name: MappingProxyType(dict(values))
            for name, values in raw.items()
            if isinstance(values, dict)
        }
        if raw.get("schema_version") != 1 or raw.get("locale") != "ru":
            raise CopyConfigurationError("Expected schema_version=1 and locale=ru")
        catalog = cls(MappingProxyType(sections))
        for section, key in sorted(STATIC_KEYS | set(REQUIRED)):
            text = catalog.value(section, key)
            actual = frozenset(PLACEHOLDER.findall(text))
            expected = REQUIRED.get((section, key), frozenset())
            if actual != expected:
                raise CopyConfigurationError(
                    f"{section}.{key} placeholders: expected {sorted(expected)}, got {sorted(actual)}"
                )
        for key, label in catalog.sections["buttons"].items():
            if not 1 <= len(label) <= 64:
                raise CopyConfigurationError(f"buttons.{key} must contain 1..64 characters")
        return catalog

    def value(self, section: str, key: str) -> str:
        try:
            value = self.sections[section][key]
        except KeyError as exc:
            raise CopyConfigurationError(f"Missing copy key: {section}.{key}") from exc
        if not isinstance(value, str) or not value:
            raise CopyConfigurationError(f"Copy key must be non-empty: {section}.{key}")
        return value

    def render(self, section: str, key: str, **values: object) -> str:
        template = self.value(section, key)
        required = frozenset(PLACEHOLDER.findall(template))
        supplied = frozenset(values)
        if required != supplied:
            raise CopyConfigurationError(
                f"{section}.{key} values: missing {sorted(required - supplied)}, "
                f"unexpected {sorted(supplied - required)}"
            )
        return PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), template)
```

- [ ] **Step 4: Run catalog tests and all configuration tests**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_content.py tests/unit/test_config.py -v`

Expected: all tests pass; loading a malformed catalog fails before any service is constructed.

- [ ] **Step 5: Commit the catalog loader**

```bash
git add app/content.py content/bot_copy.ru.toml tests/unit/test_content.py
git commit -m "feat: load bot copy from validated toml"
```

---

### Task 2: Summary templates and safe HTML rendering

**Files:**
- Modify: `app/services/summaries.py`
- Test: `tests/unit/test_summary_service.py`

**Interfaces:**
- Consumes: `BotCopy` from Task 1.
- Produces: `SummaryService(..., copy: BotCopy)`, HTML-safe `GeneratedSummary.text`, `_telegram_safe_html(text: str) -> str`.

- [ ] **Step 1: Add failing tests for the hardcoded template, prompts and escaping**

```python
from dataclasses import replace
from pathlib import Path

from app.content import BotCopy


def make_service(count: int, llm: FakeLlm | None = None):
    summaries = FakeSummaries()
    selected_llm = llm or FakeLlm(
        '{"headline":"Главное","topics":[{"title":"Релиз","details":"Договорились о запуске"}]}'
    )
    return (
        SummaryService(
            messages=FakeMessages(count),
            summaries=summaries,
            llm=selected_llm,
            copy=BotCopy.load(Path("content/bot_copy.ru.toml")),
            timezone_name="Europe/Moscow",
            automatic_min_messages=15,
            on_demand_min_messages=5,
        ),
        selected_llm,
        summaries,
    )


def test_model_summary_uses_copy_template_and_escapes_dynamic_html() -> None:
    llm = FakeLlm(
        '{"headline":"A < B & C","topics":[{"title":"<релиз>","details":"готово & принято"}]}'
    )
    service, _, _ = make_service(5, llm)

    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert result.text == (
        "<b>📝 Краткая сводка за сегодня</b>\n\n"
        "A &lt; B &amp; C\n\n"
        "• &lt;релиз&gt;\nготово &amp; принято"
    )
    assert llm.system_prompts[0] == service.copy.value("prompts", "system")


def test_fallback_escapes_names_and_message_fragments() -> None:
    llm = FakeLlm()
    llm.error = TimeoutError("timeout")
    service, _, _ = make_service(5, llm)
    service.messages.items[-1] = replace(
        service.messages.items[-1],
        full_name="Pavel <admin>",
        text="2 < 3 & это важно",
    )
    result = service.generate(-1001, SummaryKind.ON_DEMAND, 42, NOW)

    assert result.text.startswith("<b>📝 Краткая сводка за сегодня</b>")
    assert "Pavel &lt;admin&gt;" in result.text
    assert "2 &lt; 3 &amp; это важно" in result.text


def test_html_truncation_does_not_cut_entity() -> None:
    text = "<b>title</b>\n\n" + ("&amp;" * 1000)

    result = _telegram_safe_html(text)

    assert len(result) <= 3900
    assert not result.removesuffix("…").endswith(("&", "&a", "&am", "&amp"))
```

- [ ] **Step 2: Run focused tests and verify old hardcoded output fails**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_summary_service.py -v`

Expected: failures show the old `📝 Сводка за сегодня`, unescaped `<`/`&`, and hardcoded prompts.

- [ ] **Step 3: Inject `BotCopy` and render only escaped dynamic values**

```python
import html


class SummaryService:
    def __init__(self, *, messages, summaries, llm, copy, timezone_name,
                 automatic_min_messages, on_demand_min_messages) -> None:
        self.messages = messages
        self.summaries = summaries
        self.llm = llm
        self.copy = copy
        self.timezone = ZoneInfo(timezone_name)
        self.automatic_min_messages = automatic_min_messages
        self.on_demand_min_messages = on_demand_min_messages


def _escaped(value: object) -> str:
    return html.escape(str(value), quote=False)


def _render_payload(payload: dict[str, Any], copy: BotCopy) -> str:
    topics = [
        copy.render(
            "summary",
            "topic",
            topic_title=_escaped(topic["title"]),
            topic_details=_escaped(topic["details"]),
        )
        for topic in payload["topics"]
    ]
    text = copy.render(
        "summary",
        "document",
        headline=_escaped(payload["headline"]),
        topics_block=copy.value("summary", "topics_separator").join(topics),
    ).rstrip()
    return _telegram_safe_html(text)


def _telegram_safe_html(text: str) -> str:
    if len(text) <= TELEGRAM_SUMMARY_LIMIT:
        return text
    cut = text[: TELEGRAM_SUMMARY_LIMIT - 1].rstrip()
    if cut.rfind("&") > cut.rfind(";"):
        cut = cut[: cut.rfind("&")].rstrip()
    return cut + "…"
```

Record `system_prompt` and `user_prompt` in `FakeLlm.generate_summary` before returning. In production, call `copy.value("prompts", "system")`; render `prompts.transcript_line`, `prompts.user` and `prompts.repair`; call `_render_payload(payload, copy)`; and build fallback `recent_messages` from escaped `fallback.message_item` values joined by `fallback.messages_separator`. Escape `headline`, topic values, participant names and recent message text before inserting them; do not escape trusted HTML read from the TOML template.

- [ ] **Step 4: Run summary and lifecycle unit tests**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_summary_service.py tests/unit/test_lifecycle_service.py -v`

Expected: all tests pass and every generated summary starts with `<b>📝 Краткая сводка за сегодня</b>`.

- [ ] **Step 5: Commit HTML summary rendering**

```bash
git add app/services/summaries.py tests/unit/test_summary_service.py
git commit -m "feat: render summaries from copy catalog"
```

---

### Task 3: Telegram boundary for HTML, shared chats and membership

**Files:**
- Modify: `app/domain/models.py`
- Modify: `app/domain/ports.py`
- Modify: `app/integrations/telegram.py`
- Modify: `app/services/telegram_effects.py`
- Test: `tests/unit/test_telegram_parsing.py`
- Test: `tests/contract/test_telegram_client.py`
- Test: `tests/unit/test_telegram_effects.py`

**Interfaces:**
- Produces: `TelegramMembership(status: str, is_member: bool)`, `IncomingChatShared`, `IncomingChatMember`, `TelegramGateway.send_message(..., parse_mode: str | None = None)`, `TelegramGateway.get_chat_member(...) -> TelegramMembership`.

- [ ] **Step 1: Add failing parser and client contract tests**

```python
def test_parse_private_chat_shared() -> None:
    update = parse_update({
        "update_id": 20,
        "message": {
            "message_id": 30,
            "date": 1784210000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "first_name": "Pavel"},
            "chat_shared": {"request_id": -77, "chat_id": -1001, "title": "Demo"},
        },
    })

    assert update.chat_shared.request_id == -77
    assert update.chat_shared.shared_chat_id == -1001
    assert update.chat_shared.user_id == 42


def test_parse_restricted_membership_uses_is_member() -> None:
    update = parse_update({
        "update_id": 21,
        "chat_member": {
            "chat": {"id": -1001, "type": "supergroup"},
            "from": {"id": 7, "first_name": "Admin"},
            "date": 1784210000,
            "old_chat_member": {"user": {"id": 42}, "status": "member"},
            "new_chat_member": {
                "user": {"id": 42, "first_name": "Pavel"},
                "status": "restricted",
                "is_member": False,
            },
        },
    })

    assert update.chat_member.new_membership.status == "restricted"
    assert update.chat_member.new_membership.is_member is False


def test_send_message_includes_html_parse_mode(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"ok": True, "result": {"message_id": 55}})

    monkeypatch.setattr("app.integrations.telegram.urllib.request.urlopen", fake_urlopen)

    TelegramClient("token").send_message(42, "<b>title</b>", parse_mode="HTML")

    assert captured["body"]["parse_mode"] == "HTML"
```

- [ ] **Step 2: Run boundary tests and verify missing dataclasses/signatures**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_telegram_parsing.py tests/contract/test_telegram_client.py tests/unit/test_telegram_effects.py -v`

Expected: failures reference missing `chat_shared`, `chat_member`, `parse_mode`, and structured membership.

- [ ] **Step 3: Implement the typed boundary and include parse mode in the effect hash**

```python
@dataclass(frozen=True)
class TelegramMembership:
    status: str
    is_member: bool

    @property
    def can_read(self) -> bool:
        return self.status in {"creator", "administrator", "member"} or (
            self.status == "restricted" and self.is_member
        )

    @property
    def is_admin(self) -> bool:
        return self.status in {"creator", "administrator"}


@dataclass(frozen=True)
class IncomingChatShared:
    request_id: int
    shared_chat_id: int
    title: str
    user_id: int
    private_chat_id: int


@dataclass(frozen=True)
class IncomingChatMember:
    chat_id: int
    user_id: int
    username: str
    full_name: str
    new_membership: TelegramMembership
```

Add `chat_shared: IncomingChatShared | None = None` and `chat_member: IncomingChatMember | None = None` to `IncomingUpdate`. Parse `restricted.is_member` exactly; set `is_member=True` for `creator`, `administrator`, and `member`, and false for `left` and `kicked`. Make `TelegramClient.get_chat_member` return `TelegramMembership(str(result["status"]), bool(result.get("is_member", result["status"] in {"creator", "administrator", "member"})))`. Add optional `parse_mode` to raw and durable gateways and include it in `_payload_hash`:

```python
payload_hash = _payload_hash({
    "chat_id": chat_id,
    "text": text,
    "reply_markup": reply_markup,
    "parse_mode": parse_mode,
})
```

- [ ] **Step 4: Run all Telegram boundary tests**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_telegram_parsing.py tests/contract/test_telegram_client.py tests/unit/test_telegram_effects.py -v`

Expected: all tests pass, including recovery using the same payload hash when `parse_mode="HTML"`.

- [ ] **Step 5: Commit Telegram boundary changes**

```bash
git add app/domain app/integrations/telegram.py app/services/telegram_effects.py tests/unit/test_telegram_parsing.py tests/contract/test_telegram_client.py tests/unit/test_telegram_effects.py
git commit -m "feat: support telegram onboarding updates"
```

---

### Task 4: Durable onboarding data

**Files:**
- Create: `migrations/002_private_summary_onboarding.sql`
- Modify: `app/domain/ports.py`
- Modify: `app/repositories/mysql.py`
- Test: `tests/integration/test_migrations.py`
- Test: `tests/integration/test_mysql_repositories.py`

**Interfaces:**
- Produces: `ChatRepository.enable(...) -> str`, `resolve_private_link(token) -> tuple[int, str] | None`, `ChatShareRequestRepository.create(user_id, now) -> int`, `consume(request_id, user_id, now) -> bool`, `MemberRepository.remember_group(...)`, `record_membership(...)`.

- [ ] **Step 1: Add failing migration and repository tests**

```python
def test_private_onboarding_migration_is_additive() -> None:
    files = migration_files(Path("migrations"))
    assert [path.name for path in files] == [
        "001_initial.sql",
        "002_private_summary_onboarding.sql",
    ]
    sql = files[1].read_text(encoding="utf-8")
    assert "ADD COLUMN private_link_token CHAR(32) NULL" in sql
    assert "MODIFY private_link_token CHAR(32) NOT NULL" in sql
    assert "UNIQUE KEY uq_tracked_chats_private_link" in sql
    assert "CREATE TABLE chat_share_requests" in sql


def test_chat_repository_resolves_private_token() -> None:
    connection = RecordingConnection(
        fetchone_result={"chat_id": -1001, "title": "Demo", "private_link_token": "a" * 32}
    )
    repository = MySqlTrackedChatRepository(lambda: connection)

    assert repository.resolve_private_link("a" * 32) == (-1001, "Demo")


def test_share_request_is_consumed_once() -> None:
    connection = RecordingConnection(rowcount=1)
    repository = MySqlChatShareRequestRepository(lambda: connection)

    assert repository.consume(-77, 42, NOW) is True
    sql = connection.cursor_instance.executions[0][0]
    assert "used_at IS NULL" in sql
    assert "expires_at >=" in sql


def test_departure_revokes_but_does_not_delete_known_member() -> None:
    connection = RecordingConnection(rowcount=1)
    repository = MySqlMemberRepository(lambda: connection)

    repository.record_membership(-1001, 42, "left", False, NOW)

    sql = connection.cursor_instance.executions[0][0]
    assert "summary_linked_at = NULL" in sql
```

- [ ] **Step 2: Run migration/repository tests and verify schema/API failures**

Run: `.venv-0.2/bin/python -m pytest tests/integration/test_migrations.py tests/integration/test_mysql_repositories.py -v`

Expected: tests fail because migration 002 and repository methods do not exist.

- [ ] **Step 3: Add migration and transactional repository methods**

```sql
-- migrations/002_private_summary_onboarding.sql
ALTER TABLE tracked_chats ADD COLUMN private_link_token CHAR(32) NULL;
UPDATE tracked_chats
SET private_link_token = REPLACE(UUID(), '-', '')
WHERE private_link_token IS NULL;
ALTER TABLE tracked_chats MODIFY private_link_token CHAR(32) NOT NULL;
ALTER TABLE tracked_chats ADD UNIQUE KEY uq_tracked_chats_private_link (private_link_token);

CREATE TABLE chat_share_requests (
    request_id INT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    used_at DATETIME(6),
    created_at DATETIME(6) NOT NULL,
    KEY idx_chat_share_requests_user (user_id, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`MySqlTrackedChatRepository.enable` creates `secrets.token_hex(16)` for new rows, preserves an existing token during re-enable, selects it in the same managed connection, and returns it. `MySqlChatShareRequestRepository.create` generates values with `secrets.randbelow(2**32) - 2**31`, retries duplicate-key inserts up to five times, and expires after one hour. `consume` performs one conditional `UPDATE ... SET used_at = %s WHERE request_id = %s AND user_id = %s AND expires_at >= %s AND used_at IS NULL` and returns `cursor.rowcount == 1`.

`remember_group` executes an `INSERT ... ON DUPLICATE KEY UPDATE` that sets `username`, `full_name`, `last_seen_at`, `summary_linked_at`, `last_membership_check_at`, and active `last_membership_status`. `record_membership` executes `UPDATE chat_members SET last_membership_check_at=%s, last_membership_status=%s, summary_linked_at=CASE WHEN %s = 1 THEN summary_linked_at ELSE NULL END WHERE chat_id=%s AND user_id=%s`, so unknown users are not inserted. Add `chat_share_requests: DELETE FROM chat_share_requests WHERE expires_at < %s` to `MySqlMaintenanceStore.purge_expired`.

- [ ] **Step 4: Verify migration ordering and repository contracts**

Run: `.venv-0.2/bin/python -m pytest tests/integration/test_migrations.py tests/integration/test_mysql_repositories.py tests/unit/test_audit_service.py -v`

Expected: all tests pass; migration files are ordered 001 then 002, and repository tests prove token preservation, atomic request consumption and membership revocation SQL.

- [ ] **Step 5: Commit durable onboarding data**

```bash
git add migrations/002_private_summary_onboarding.sql app/domain/ports.py app/repositories/mysql.py tests/integration/test_migrations.py tests/integration/test_mysql_repositories.py tests/unit/test_audit_service.py
git commit -m "feat: persist private summary onboarding"
```

---

### Task 5: Group enablement, bot-admin check and passive discovery

**Files:**
- Modify: `app/services/access.py`
- Modify: `app/services/group_updates.py`
- Test: `tests/unit/test_access_service.py`
- Test: `tests/e2e/test_group_commands.py`

**Interfaces:**
- Consumes: `BotCopy`, bot numeric ID, token-returning `ChatRepository.enable`.
- Produces: onboarding deep link `https://t.me/{username}?start=summary_{token}` and automatic `remember_group` for ordinary tracked-group messages.

- [ ] **Step 1: Write failing group behavior tests**

```python
def test_enable_requires_bot_admin_before_database_change() -> None:
    telegram = FakeTelegram({42: TelegramMembership("administrator", True), 777: TelegramMembership("member", True)})
    service = AccessService(FakeChats(), telegram, bot_user_id=777)

    with pytest.raises(AccessDenied) as raised:
        service.enable_daily(-1001, 42, "Demo", NOW)

    assert raised.value.copy_key == "bot_admin_required"


def test_enable_publishes_private_summary_deep_link() -> None:
    handler, chats, members, messages, telegram, requests = build_handler()
    chats.next_token = "a" * 32

    handler(parse_update(group_update("/enable_daily")))

    chat_id, text, markup, parse_mode = telegram.sent[-1]
    assert text.startswith("Готово — ежедневные сводки включены")
    assert markup["inline_keyboard"][0][0] == {
        "text": "Получить сводку лично",
        "url": "https://t.me/BrevitySisterBot?start=summary_" + "a" * 32,
    }


def test_ordinary_message_passively_remembers_group() -> None:
    handler, chats, members, messages, telegram, requests = build_handler()
    chats.tracked.add(-1001)

    handler(parse_update(group_update("Обычное сообщение")))

    assert members.remembered == [(-1001, 42)]
    assert messages.saved[0].text == "Обычное сообщение"
```

- [ ] **Step 2: Run access/group tests and verify failures**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_access_service.py tests/e2e/test_group_commands.py -v`

Expected: failures show no bot-admin check, no returned token/button, and no passive summary link.

- [ ] **Step 3: Make access errors symbolic and publish onboarding idempotently**

```python
class AccessDenied(RuntimeError):
    def __init__(self, copy_key: str) -> None:
        super().__init__(copy_key)
        self.copy_key = copy_key


class AccessService:
    def __init__(self, chats, telegram, bot_user_id: int) -> None:
        self.chats = chats
        self.telegram = telegram
        self.bot_user_id = bot_user_id

    def enable_daily(self, chat_id, actor_id, title, now) -> str:
        if not self.telegram.get_chat_member(chat_id, actor_id).is_admin:
            raise AccessDenied("admin_required")
        if not self.telegram.get_chat_member(chat_id, self.bot_user_id).is_admin:
            raise AccessDenied("bot_admin_required")
        return self.chats.enable(chat_id, title, now)
```

Inject `access`, `copy`, and `telegram_bot_username` into `GroupUpdateHandler`; remove embedded user strings. On successful `/enable_daily`, send `messages.daily_enabled` with the inline URL button. The durable gateway keeps this send idempotent for a retried update. For every ordinary text message in a tracked group, call `members.remember_group(...)` before storing it. `/summary` also calls the same method. `/disable_daily` uses `messages.daily_disabled` and does not revoke the link token.

- [ ] **Step 4: Run group tests and webhook idempotency regression**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_access_service.py tests/e2e/test_group_commands.py tests/e2e/test_webhook_flow.py tests/unit/test_telegram_effects.py -v`

Expected: all tests pass and replaying `/enable_daily` through recovery does not create a second Telegram send effect.

- [ ] **Step 5: Commit group onboarding**

```bash
git add app/services/access.py app/services/group_updates.py tests/unit/test_access_service.py tests/e2e/test_group_commands.py tests/e2e/test_webhook_flow.py
git commit -m "feat: publish private summary onboarding"
```

---

### Task 6: Frictionless private summary flow

**Files:**
- Modify: `app/services/on_demand.py`
- Test: `tests/e2e/test_private_summary.py`
- Test: `tests/e2e/test_on_demand_summary.py`

**Interfaces:**
- Consumes: `ChatRepository`, `MemberRepository`, `ChatShareRequestRepository`, `SummarySelectionRepository`, `BotCopy`, bot numeric ID.
- Produces: direct `/summary`, `/start summary_{token}`, `chat_shared`, native picker markup, filtered inline selection and immediate private generation.

- [ ] **Step 1: Add failing end-to-end scenarios**

```python
def test_deep_link_remembers_group_and_immediately_generates() -> None:
    handler, telegram, summaries, members = make_handler()
    chats.private_links["a" * 32] = (-1001, "Demo")
    telegram.memberships[-1001, 42] = TelegramMembership("member", True)
    telegram.memberships[-1001, 777] = TelegramMembership("administrator", True)

    handler(private_message("/start summary_" + "a" * 32))

    assert members.remembered == [(-1001, 42)]
    assert summaries.calls[0][0:3] == (-1001, SummaryKind.ON_DEMAND, 42)


def test_no_known_groups_shows_native_picker() -> None:
    handler, telegram, summaries, members = make_handler()
    share_requests.next_id = -77

    handler(private_message("/summary"))

    button = telegram.sent[-1][2]["keyboard"][0][0]
    assert button["text"] == "Выбрать группу"
    assert button["request_chat"] == {
        "request_id": -77,
        "chat_is_channel": False,
        "bot_is_member": True,
        "request_title": True,
    }


def test_valid_chat_shared_removes_keyboard_and_generates() -> None:
    handler, telegram, summaries, members = make_handler()
    share_requests.consumable.add((-77, 42))
    chats.tracked[-1001] = "Demo"
    telegram.memberships[-1001, 42] = TelegramMembership("member", True)
    telegram.memberships[-1001, 777] = TelegramMembership("administrator", True)

    handler(chat_shared_update(request_id=-77, shared_chat_id=-1001, user_id=42))

    assert telegram.sent[0][2] == {"remove_keyboard": True}
    assert members.remembered == [(-1001, 42)]
    assert summaries.calls[0][0] == -1001


def test_known_groups_are_rechecked_before_buttons_are_shown() -> None:
    members.groups = [(-1001, "Visible"), (-1002, "Departed")]
    telegram.memberships[-1001, 42] = TelegramMembership("member", True)
    telegram.memberships[-1002, 42] = TelegramMembership("left", False)
    telegram.memberships[-1001, 777] = TelegramMembership("administrator", True)
    telegram.memberships[-1002, 777] = TelegramMembership("administrator", True)

    handler(private_message("/summary"))

    assert summaries.calls[0][0] == -1001
    assert all("Departed" not in sent[1] for sent in telegram.sent)
```

- [ ] **Step 2: Run private-flow tests and verify the old ritual fails them**

Run: `.venv-0.2/bin/python -m pytest tests/e2e/test_private_summary.py tests/e2e/test_on_demand_summary.py -v`

Expected: failures show the old “Сначала запросите /summary внутри нужной группы”, missing picker/deep-link handling, and unfiltered remembered groups.

- [ ] **Step 3: Implement one verification path for every private entry point**

```python
def _membership_allowed(membership: TelegramMembership) -> bool:
    return membership.can_read


def _private_start_token(text: str) -> str | None:
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0].split("@", 1)[0].lower() != "/start" or len(parts) != 2:
        return None
    payload = parts[1]
    return payload.removeprefix("summary_") if payload.startswith("summary_") else None


def _picker_markup(request_id: int, label: str) -> dict:
    return {
        "keyboard": [[{
            "text": label,
            "request_chat": {
                "request_id": request_id,
                "chat_is_channel": False,
                "bot_is_member": True,
                "request_title": True,
            },
        }]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
```

Add `_verify_and_deliver(chat_id: int, user_id: int, username: str, full_name: str, destination_chat_id: int, remove_keyboard: bool = False) -> None` to `OnDemandSummaryHandler`. It checks `get_chat_member(chat_id, bot_user_id).is_admin`, then `get_chat_member(chat_id, user_id).can_read`, calls `members.remember_group(chat_id, user_id, username, full_name, now)`, and finally calls `requests.request(..., ack_reply_markup={"remove_keyboard": True} if remove_keyboard else None)`. Add `ack_reply_markup: dict | None = None` to `OnDemandSummaryService.request` and pass it only to the acceptance send.

For direct `/summary`, recheck both bot and user for every remembered group: zero valid groups creates and shows a native picker; one immediately delivers; several create existing opaque one-hour inline selection tokens. Unknown/invalid deep links show `private_link_invalid` plus the picker. A valid but untracked shared group shows `selected_group_not_connected`; missing bot admin shows `group_access_unverifiable`; missing membership shows `membership_not_confirmed`. Telegram API errors propagate into the existing update retry flow.

- [ ] **Step 4: Run private, group and summary regression tests**

Run: `.venv-0.2/bin/python -m pytest tests/e2e/test_private_summary.py tests/e2e/test_on_demand_summary.py tests/e2e/test_group_commands.py tests/unit/test_summary_service.py -v`

Expected: all tests pass; no test or production module contains the removed group-ritual phrase.

- [ ] **Step 5: Commit private onboarding**

```bash
git add app/services/on_demand.py tests/e2e/test_private_summary.py tests/e2e/test_on_demand_summary.py
git commit -m "feat: add frictionless private summaries"
```

---

### Task 7: Membership events and production wiring

**Files:**
- Create: `app/services/membership_updates.py`
- Modify: `app/config.py`
- Modify: `app/web/production.py`
- Modify: `app/jobs/cli.py`
- Modify: `app/services/lifecycle.py`
- Modify: `scripts/set_webhook.py`
- Modify: `hosting.env.example`
- Test: `tests/unit/test_membership_updates.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_lifecycle_service.py`
- Test: `tests/contract/test_telegram_client.py`

**Interfaces:**
- Consumes: all Tasks 1–6.
- Produces: startup-fast-fail copy loading, `TELEGRAM_BOT_USERNAME`, `chat_member` dispatch and HTML scheduled publication.

- [ ] **Step 1: Add failing wiring-level tests**

```python
def test_departure_event_revokes_known_private_access() -> None:
    members = FakeMembers()
    handler = MembershipUpdateHandler(members=members, now=lambda: NOW)

    handler(IncomingUpdate(
        update_id=30,
        chat_member=IncomingChatMember(
            chat_id=-1001,
            user_id=42,
            username="@pavel",
            full_name="Pavel",
            new_membership=TelegramMembership("left", False),
        ),
    ))

    assert members.recorded == [(-1001, 42, "left", False)]


def test_settings_require_bot_username(valid_environment) -> None:
    valid_environment.pop("TELEGRAM_BOT_USERNAME")

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_USERNAME"):
        load_settings(valid_environment)


def test_scheduled_publication_uses_html_parse_mode() -> None:
    service, store, telegram = make_lifecycle_service()
    store.publications = [(1, -1001, "<b>📝 Краткая сводка за сегодня</b>")]

    service.publish_day(NOW)

    assert telegram.sent[-1].parse_mode == "HTML"
```

- [ ] **Step 2: Run wiring tests and verify missing handler/configuration**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_membership_updates.py tests/unit/test_config.py tests/unit/test_lifecycle_service.py -v`

Expected: missing module/setting failures and scheduled send without HTML parse mode.

- [ ] **Step 3: Wire one catalog and all handlers in web and recovery composition roots**

```python
# app/services/membership_updates.py
class MembershipUpdateHandler:
    def __init__(self, *, members: MemberRepository, now: NowFactory) -> None:
        self.members = members
        self.now = now

    def __call__(self, update: IncomingUpdate) -> None:
        event = update.chat_member
        if event is None:
            return
        self.members.record_membership(
            event.chat_id,
            event.user_id,
            event.new_membership.status,
            event.new_membership.is_member,
            self.now(),
        )
```

Add `telegram_bot_username: str` after `telegram_bot_token` in `Settings`; include `TELEGRAM_BOT_USERNAME` in `required`; load it with `values["TELEGRAM_BOT_USERNAME"].strip().removeprefix("@")`; reject the post-strip empty string. Derive `bot_user_id = int(settings.telegram_bot_token.split(":", 1)[0])` in both composition roots. Load `BotCopy` once from `PROJECT_ROOT / "content" / "bot_copy.ru.toml"`; inject the same immutable instance into summaries and handlers. Construct `MySqlChatShareRequestRepository`, `AccessService`, group/private/membership handlers and call all three for each parsed update.

Change scheduled publication to `telegram.send_message(chat_id, text, parse_mode="HTML")`. Change `scripts/set_webhook.py` to `allowed_updates = ["message", "callback_query", "chat_member"]`. Add `TELEGRAM_BOT_USERNAME=BrevitySisterBot` to `hosting.env.example` without `@` and without adding it to `.env` files committed to Git.

- [ ] **Step 4: Run composition-adjacent tests and import production safely**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_membership_updates.py tests/unit/test_config.py tests/unit/test_lifecycle_service.py tests/e2e/test_webhook_flow.py -v`

Expected: all tests pass; webhook flow dispatches each update once and scheduled summaries use HTML.

Run: `.venv-0.2/bin/python -m compileall -q app scripts public_html passenger_wsgi.py`

Expected: exit code 0.

- [ ] **Step 5: Commit production wiring**

```bash
git add app/config.py app/web/production.py app/jobs/cli.py app/services/membership_updates.py app/services/lifecycle.py scripts/set_webhook.py hosting.env.example tests
git commit -m "feat: wire private summary onboarding"
```

---

### Task 8: Generated copy deck, deployment safety and release verification

**Files:**
- Create: `scripts/render_copy_deck.py`
- Modify: `docs/BOT_COPY_RU.md`
- Modify: `README.md`
- Modify: `tests/unit/test_deploy_safety.py`
- Create: `tests/unit/test_copy_deck.py`

**Interfaces:**
- Consumes: final `BotCopy` catalog and all production code.
- Produces: reproducible editor-facing copy deck and verified Beget deploy contents.

- [ ] **Step 1: Write failing synchronization and deploy-content tests**

```python
def test_copy_deck_is_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/render_copy_deck.py", "--check"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_deploy_script_does_not_exclude_runtime_content() -> None:
    script = Path("scripts/deploy_beget.exp").read_text(encoding="utf-8")

    assert "--exclude content" not in script
    assert "./ $target" in script
```

- [ ] **Step 2: Run documentation/deploy tests and verify missing renderer**

Run: `.venv-0.2/bin/python -m pytest tests/unit/test_copy_deck.py tests/unit/test_deploy_safety.py -v`

Expected: failure because `scripts/render_copy_deck.py` does not exist.

- [ ] **Step 3: Implement deterministic Markdown rendering and document operations**

```python
def render(copy: BotCopy) -> str:
    contexts = {
        "group_commands": "Пользователь отправляет /start в группе.",
        "daily_enabled": "Администратор успешно отправил /enable_daily.",
        "daily_disabled": "Администратор успешно отправил /disable_daily.",
        "group_not_connected": "Участник вызывает /summary в неподключённой группе.",
        "admin_required": "Не-администратор меняет настройку ежедневной сводки.",
        "summary_accepted": "Ручной запрос принят до обращения к модели.",
        "summary_in_progress": "Тот же набор сообщений уже обрабатывается.",
        "not_enough_messages": "Для ручной сводки накоплено меньше пяти сообщений.",
        "choose_group_native": "В личном чате нет запомненной доступной группы.",
        "choose_group_known": "В личном чате доступно несколько групп.",
        "group_choice_expired": "Одночасовой выбор группы недействителен.",
        "membership_not_confirmed": "Telegram не подтверждает членство перед выдачей.",
        "bot_admin_required": "Бот не администратор при /enable_daily.",
        "private_link_invalid": "Групповая deep link-ссылка неизвестна или повреждена.",
        "selected_group_not_connected": "Выбранная через Telegram группа не подключена.",
        "group_access_unverifiable": "Бот не может надёжно проверить выбранную группу.",
    }
    sections = ["# BrevitySisterBot 0.2.0 — вординг и промпты", ""]
    for section in ("messages", "buttons", "summary", "fallback", "prompts"):
        sections.extend((f"## {section}", ""))
        for key, value in copy.sections[section].items():
            context = contexts.get(key)
            sections.extend((f"### `{section}.{key}`", ""))
            if context:
                sections.extend((f"Контекст: {context}", ""))
            sections.extend(("```text", value, "```", ""))
    return "\n".join(sections).rstrip() + "\n"
```

The script loads `content/bot_copy.ru.toml`, writes `docs/BOT_COPY_RU.md` in normal mode, and in `--check` mode compares generated bytes without writing. Its fixed preamble contains the product constraints and ready-to-paste editor prompt; its `contexts` mapping contains only display conditions, never alternate runtime text. Runtime texts themselves come only from `BotCopy.sections`.

Update README with: bot-admin requirement; `/enable_daily` onboarding button; direct private `/summary`; `TELEGRAM_BOT_USERNAME`; `chat_member` webhook subscription; TOML edit/restart workflow; command `.venv/bin/python scripts/render_copy_deck.py --check`. State that Beget rsync already includes `content/` and pre-migration backup still runs before migration 002.

- [ ] **Step 4: Run complete release verification**

Run: `.venv-0.2/bin/python scripts/render_copy_deck.py`

Expected: `docs/BOT_COPY_RU.md` is regenerated from the approved TOML.

Run: `.venv-0.2/bin/python -m pytest -q`

Expected: all tests pass.

Run: `.venv-0.2/bin/ruff check app scripts tests passenger_wsgi.py public_html/passenger_wsgi.py`

Expected: `All checks passed!`.

Run: `.venv-0.2/bin/python -m compileall -q app scripts public_html passenger_wsgi.py`

Expected: exit code 0.

Run: `.venv-0.2/bin/python -m pip check`

Expected: `No broken requirements found.`

Run: `git diff --check && rg -n "Сначала запросите /summary внутри|📝 Сводка за сегодня|1–2 минут|буферизация" app content tests docs/BOT_COPY_RU.md`

Expected: `git diff --check` succeeds and `rg` finds no obsolete production wording.

- [ ] **Step 5: Commit documentation and verification artifacts**

```bash
git add scripts/render_copy_deck.py docs/BOT_COPY_RU.md README.md tests/unit/test_copy_deck.py tests/unit/test_deploy_safety.py
git commit -m "docs: finalize private summary onboarding"
```

---

## Production handoff after implementation

These are deployment actions, not local implementation steps. Execute them only after `.env.hosting` and the Beget MySQL credentials are present locally:

1. Add `TELEGRAM_BOT_USERNAME` without `@` to the server `.env`.
2. Run `scripts/deploy_beget.exp`; it creates the pre-migration backup, uploads `content/`, installs the editable package, applies migration 002, and touches `tmp/restart.txt`.
3. Run `.venv/bin/python scripts/set_webhook.py` on Beget so `chat_member` enters `allowed_updates`.
4. Verify `/health`, then test in one live group: bot is admin → `/enable_daily` → group button → private Start → private summary.
5. Test direct private `/summary` from a second member with no remembered group and select the group through the native picker.
6. Run `/disable_daily` and verify that private `/summary` still works.
7. Run migration and operational audit commands again; migration must report no changes and audit must report healthy storage.
