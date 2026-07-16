# BrevitySisterBot 0.2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Переписать BrevitySisterBot как версию `0.2.0` для Beget shared hosting с Python WSGI webhook, MySQL, Groq, личными сводками, безопасным архивом и восстановлением заданий.

**Architecture:** Текущий MVP `0.1.0` сохраняется в `mvp-1/`, а новый код строится в корне репозитория. Webhook сначала фиксирует Telegram update в MySQL, затем пытается обработать его сразу; recovery worker каждые пять минут подбирает прерванную работу. Бизнес-логика отделена от WSGI, Telegram, Groq и MySQL через небольшие интерфейсы.

**Tech Stack:** Python 3.11+, WSGI/Passenger, MySQL 8-compatible SQL, `PyMySQL`, `python-dotenv`, стандартный `urllib`, `pytest`, `ruff`, Beget CronTab, Groq Chat Completions API.

## Global Constraints

- Production размещается только на Beget shared hosting, без VPS и постоянно работающего polling-процесса.
- Историческая реализация `0.1.0` сохраняется в `mvp-1/`; новая версия в корне имеет номер `0.2.0`.
- BrevitySisterBot и BernsBot используют разные каталоги, домены, токены, webhook secrets и MySQL-базы.
- Webhook сохраняет update до бизнес-обработки; повторный `update_id` не создаёт повторную работу.
- Сообщение уникально по `(chat_id, telegram_message_id)`.
- Автоматическая сводка: минимум 15 сообщений, подготовка в `20:45`, публикация в `21:00` по Москве.
- Ручная групповая и личная сводка: минимум пять сообщений.
- `/enable_daily` и `/disable_daily` доступны только администратору группы.
- Личная сводка требует проверки текущего членства через `getChatMember`.
- Основная модель: `qwen/qwen3.6-27b`, задаётся через `GROQ_MODEL`; Groq используется с Zero Data Retention.
- При ошибке Groq обязателен детерминированный эвристический fallback.
- Рабочие сообщения удаляются только после идемпотентного копирования, проверки архива и фиксации lifecycle.
- Архив, update diagnostics, логи и backup хранятся не более 30 дней.
- Никакие production secrets, SQLite-файлы, backup, логи и `.superpowers/` не попадают в коммиты.
- Каждый этап следует TDD: падающий тест, минимальная реализация, проходящий тест, отдельный коммит.

---

## Карта файлов новой версии

```text
app/
  __init__.py                  # версия приложения
  config.py                    # Settings и загрузка environment
  domain/
    models.py                  # dataclass-модели и enum состояний
    ports.py                   # Protocol-интерфейсы repositories и clients
  repositories/
    mysql.py                   # MySQL repositories и транзакции
  services/
    updates.py                 # claim/process/retry Telegram updates
    access.py                  # admin/member проверки и связи групп
    summaries.py               # пороги, кеш, Groq и fallback
    lifecycle.py               # prepare/publish/archive/cleanup
  integrations/
    telegram.py                # Bot API HTTP client и parsing updates
    groq.py                    # Groq Chat Completions client
  web/
    app.py                     # WSGI application
  jobs/
    cli.py                     # recovery и расписание
    audit.py                   # operational audit
migrations/
  001_initial.sql              # production MySQL schema
scripts/
  migrate.py                   # применение миграций
  import_mvp.py                # одноразовый SQLite → MySQL import
  deploy_beget.exp             # безопасный deploy
  backup_db.sh                 # dump и 30-day rotation
  set_webhook.py               # установка Telegram webhook
  smoke_preview.py             # preview smoke checks
tests/
  unit/
  integration/
  contract/
  e2e/
passenger_wsgi.py              # Passenger entry point
pyproject.toml
.env.example
hosting.env.example
```

## Task 1: Сохранить MVP `0.1.0` и создать чистый корень `0.2.0`

**Files:**
- Create: `mvp-1/README.md`
- Move: текущие Python-модули, MVP-тесты и MVP-документацию в `mvp-1/`
- Modify: `.gitignore`
- Modify: `README.md`
- Create: `app/__init__.py`
- Create: `pyproject.toml`
- Create: `tests/unit/test_version.py`

**Interfaces:**
- Produces: `app.__version__: str == "0.2.0"`
- Preserves: запускаемый и тестируемый snapshot MVP внутри `mvp-1/`

- [ ] **Step 1: Зафиксировать исходное состояние без изменения пользовательских данных**

Run:

```bash
git status --short
python3 -m unittest discover -s tests -v
```

Expected: текущие 13 MVP-тестов проходят; список незакоммиченных файлов записан в рабочие заметки. `bot.db`, `archive.db`, `.env`, `.superpowers/` не добавляются в индекс.

- [ ] **Step 2: Написать падающий тест версии**

```python
# tests/unit/test_version.py
from app import __version__


def test_application_version_is_0_2_0() -> None:
    assert __version__ == "0.2.0"
```

- [ ] **Step 3: Запустить тест и подтвердить падение**

Run: `pytest tests/unit/test_version.py -v`

Expected: FAIL с `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 4: Перенести MVP и создать минимальный пакет**

`app/__init__.py`:

```python
__version__ = "0.2.0"
```

`mvp-1/README.md`:

```markdown
# BrevitySisterBot MVP 0.1.0

Исторический snapshot cron/SQLite/Ollama-реализации до rewrite `0.2.0`.
Секреты, runtime-базы, логи и virtualenv в snapshot не входят.

Запуск тестов:

```bash
python3 -m unittest discover -s mvp-1/tests -v
```
```

В `.gitignore` добавить:

```gitignore
.superpowers/
*.sqlite3
*.sqlite3.*
backups/
.pytest_cache/
.ruff_cache/
```

Новый `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "brevity-sister-bot"
version = "0.2.0"
requires-python = ">=3.11"
dependencies = [
  "PyMySQL==1.1.2",
  "python-dotenv==1.1.1",
]

[project.optional-dependencies]
dev = [
  "pytest==8.4.2",
  "ruff==0.15.20",
]

[tool.setuptools.packages.find]
include = ["app*"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 5: Проверить новый корень и исторический snapshot**

Run:

```bash
pytest tests/unit/test_version.py -v
python3 -m unittest discover -s mvp-1/tests -v
git status --short
```

Expected: новый тест PASS; MVP-тесты PASS; runtime-файлы не staged.

- [ ] **Step 6: Commit**

```bash
git add .gitignore README.md pyproject.toml app tests/unit mvp-1
git commit -m "chore: preserve MVP and start 0.2.0"
```

## Task 2: Конфигурация и доменные контракты

**Files:**
- Create: `app/config.py`
- Create: `app/domain/__init__.py`
- Create: `app/domain/models.py`
- Create: `app/domain/ports.py`
- Create: `.env.example`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_domain_models.py`

**Interfaces:**
- Produces: `load_settings(environ: Mapping[str, str] | None = None) -> Settings`
- Produces: `UpdateStatus`, `SummaryKind`, `SummaryStatus`, `StoredMessage`, `QueuedUpdate`, `GeneratedSummary`
- Produces: `Clock`, `UpdateRepository`, `MessageRepository`, `SummaryRepository`, `TelegramGateway`, `LlmGateway` protocols

- [ ] **Step 1: Написать падающие тесты Settings и enum**

```python
from app.config import load_settings
from app.domain.models import SummaryKind, UpdateStatus


def test_load_settings_requires_production_secrets() -> None:
    settings = load_settings({
        "TELEGRAM_BOT_TOKEN": "bot",
        "WEBHOOK_SECRET_TOKEN": "secret",
        "DB_HOST": "localhost",
        "DB_NAME": "brevity",
        "DB_USER": "brevity",
        "DB_PASSWORD": "password",
        "GROQ_API_KEY": "groq",
    })
    assert settings.groq_model == "qwen/qwen3.6-27b"
    assert settings.timezone == "Europe/Moscow"
    assert settings.owner_chat_id is None


def test_domain_status_values_are_stable() -> None:
    assert UpdateStatus.PENDING.value == "pending"
    assert SummaryKind.ON_DEMAND.value == "on_demand"
```

- [ ] **Step 2: Запустить тесты и подтвердить падение**

Run: `pytest tests/unit/test_config.py tests/unit/test_domain_models.py -v`

Expected: FAIL из-за отсутствующих модулей.

- [ ] **Step 3: Реализовать Settings и модели**

Ключевые сигнатуры:

```python
@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    webhook_secret_token: str
    db_host: str
    db_name: str
    db_user: str
    db_password: str
    groq_api_key: str
    groq_model: str = "qwen/qwen3.6-27b"
    timezone: str = "Europe/Moscow"
    owner_chat_id: int | None = None
    automatic_min_messages: int = 15
    on_demand_min_messages: int = 5
    archive_retention_days: int = 30


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    values = dict(os.environ if environ is None else environ)
    required = (
        "TELEGRAM_BOT_TOKEN",
        "WEBHOOK_SECRET_TOKEN",
        "DB_HOST",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "GROQ_API_KEY",
    )
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")
    return Settings(
        telegram_bot_token=values["TELEGRAM_BOT_TOKEN"].strip(),
        webhook_secret_token=values["WEBHOOK_SECRET_TOKEN"].strip(),
        db_host=values["DB_HOST"].strip(),
        db_name=values["DB_NAME"].strip(),
        db_user=values["DB_USER"].strip(),
        db_password=values["DB_PASSWORD"],
        groq_api_key=values["GROQ_API_KEY"].strip(),
        groq_model=values.get("GROQ_MODEL", "qwen/qwen3.6-27b").strip(),
        timezone=values.get("BOT_TIMEZONE", "Europe/Moscow").strip(),
        owner_chat_id=int(values["OWNER_CHAT_ID"]) if values.get("OWNER_CHAT_ID") else None,
    )
```

```python
class UpdateStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    RETRY = "retry"
    FAILED = "failed"


class SummaryKind(str, Enum):
    SCHEDULED = "scheduled"
    ON_DEMAND = "on_demand"
```

Protocols must expose exact methods used later:

```python
class TelegramGateway(Protocol):
    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> int:
        raise NotImplementedError

    def get_chat_member(self, chat_id: int, user_id: int) -> str:
        raise NotImplementedError


class LlmGateway(Protocol):
    def generate_summary(self, system_prompt: str, user_prompt: str) -> LlmResult:
        raise NotImplementedError
```

- [ ] **Step 4: Запустить unit-тесты**

Run: `pytest tests/unit/test_config.py tests/unit/test_domain_models.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/domain .env.example tests/unit
git commit -m "feat: define configuration and domain contracts"
```

## Task 3: MySQL-схема и migration runner

**Files:**
- Create: `migrations/001_initial.sql`
- Create: `scripts/migrate.py`
- Create: `app/repositories/__init__.py`
- Create: `app/repositories/mysql.py`
- Test: `tests/integration/conftest.py`
- Test: `tests/integration/test_migrations.py`
- Test: `tests/integration/test_mysql_repositories.py`

**Interfaces:**
- Produces: `MySqlDatabase.connect() -> pymysql.Connection`
- Produces: `apply_migrations(database: MySqlDatabase, directory: Path) -> list[str]`
- Produces repositories implementing Task 2 protocols

- [ ] **Step 1: Написать integration-тесты схемы**

```python
def test_initial_migration_creates_required_tables(mysql_database) -> None:
    applied = apply_migrations(mysql_database, Path("migrations"))
    assert applied == ["001_initial.sql"]
    assert mysql_database.table_names() >= {
        "telegram_updates",
        "tracked_chats",
        "chat_members",
        "messages",
        "prepared_summaries",
        "scheduled_publications",
        "archived_messages",
        "archived_summaries",
        "daily_lifecycle",
        "job_locks",
        "schema_migrations",
    }


def test_duplicate_message_is_ignored(message_repository, sample_message) -> None:
    assert message_repository.add(sample_message) is True
    assert message_repository.add(sample_message) is False
    assert message_repository.count_for_chat(sample_message.chat_id) == 1
```

- [ ] **Step 2: Запустить integration-тест и подтвердить падение**

Run: `pytest tests/integration/test_migrations.py -v`

Expected: FAIL, если `TEST_DB_*` не заданы — SKIP с понятным сообщением; в CI/preview с тестовой MySQL — FAIL из-за отсутствия migration runner.

- [ ] **Step 3: Реализовать полную начальную схему**

`001_initial.sql` должен:

- использовать `BIGINT` для Telegram IDs;
- использовать `JSON` для `payload_json`;
- создать уникальные ключи `telegram_updates(update_id)`,
  `messages(chat_id, telegram_message_id)`,
  `scheduled_publications(chat_id, summary_date)`;
- создать индекс очереди `(status, available_at, claimed_at)`;
- создать индекс сообщений `(chat_id, sent_at)`;
- создать уникальность on-demand cache по
  `(chat_id, summary_date, summary_kind, source_hash)`;
- использовать `utf8mb4`.

Migration runner выполняет каждый файл в транзакции и записывает checksum:

```python
def apply_migrations(database: MySqlDatabase, directory: Path) -> list[str]:
    applied: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        if database.is_migration_applied(path.name, sha256(path.read_bytes()).hexdigest()):
            continue
        database.execute_script(path.read_text(encoding="utf-8"))
        database.record_migration(path.name, sha256(path.read_bytes()).hexdigest())
        applied.append(path.name)
    return applied
```

- [ ] **Step 4: Реализовать repositories и проверить уникальность**

Минимальные методы:

```python
class MySqlUpdateRepository:
    def enqueue(self, update_id: int, payload: dict) -> bool:
        raise NotImplementedError

    def claim(self, update_id: int, now: datetime, lease_seconds: int) -> QueuedUpdate | None:
        raise NotImplementedError

    def claim_available(self, now: datetime, limit: int) -> list[QueuedUpdate]:
        raise NotImplementedError

    def complete(self, update_id: int, now: datetime) -> None:
        raise NotImplementedError

    def retry(
        self,
        update_id: int,
        now: datetime,
        available_at: datetime,
        error: str,
    ) -> None:
        raise NotImplementedError


class MySqlMessageRepository:
    def add(self, message: StoredMessage) -> bool:
        raise NotImplementedError

    def list_between(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> list[StoredMessage]:
        raise NotImplementedError
```

- [ ] **Step 5: Запустить integration suite**

Run: `pytest tests/integration/test_migrations.py tests/integration/test_mysql_repositories.py -v`

Expected: PASS на тестовой MySQL.

- [ ] **Step 6: Commit**

```bash
git add migrations scripts/migrate.py app/repositories tests/integration
git commit -m "feat: add MySQL schema and repositories"
```

## Task 4: Telegram HTTP client и parsing updates

**Files:**
- Create: `app/integrations/__init__.py`
- Create: `app/integrations/telegram.py`
- Test: `tests/contract/test_telegram_client.py`
- Test: `tests/unit/test_telegram_parsing.py`

**Interfaces:**
- Produces: `TelegramClient.send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> int`
- Produces: `TelegramClient.get_chat_member(chat_id, user_id) -> str`
- Produces: `parse_update(payload: dict) -> IncomingUpdate`

- [ ] **Step 1: Написать падающие parsing- и contract-тесты**

```python
def test_parse_group_text_message() -> None:
    update = parse_update({
        "update_id": 10,
        "message": {
            "message_id": 20,
            "date": 1784210000,
            "chat": {"id": -1001, "type": "supergroup", "title": "Demo"},
            "from": {"id": 30, "first_name": "Pavel", "is_bot": False},
            "text": "Привет",
        },
    })
    assert update.update_id == 10
    assert update.message.text == "Привет"
    assert update.message.chat_id == -1001
```

Contract-тест подменяет `urlopen` и проверяет URL, JSON body, timeout и
нормализацию Telegram error.

- [ ] **Step 2: Запустить тесты и подтвердить падение**

Run: `pytest tests/unit/test_telegram_parsing.py tests/contract/test_telegram_client.py -v`

Expected: FAIL из-за отсутствия integration.

- [ ] **Step 3: Реализовать клиент без Telegram framework**

Использовать стандартный HTTP boundary:

```python
class TelegramClient:
    def __init__(self, token: str, timeout_seconds: float = 10.0) -> None:
        self.token = token
        self.timeout_seconds = timeout_seconds

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
    ) -> int:
        return self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            **({"reply_markup": reply_markup} if reply_markup is not None else {}),
        })["message_id"]

    def get_chat_member(self, chat_id: int, user_id: int) -> str:
        return str(self._call("getChatMember", {
            "chat_id": chat_id,
            "user_id": user_id,
        })["status"])
```

Допустимые member statuses: `creator`, `administrator`, `member`, `restricted`.
`left` и `kicked` запрещают выдачу личной сводки.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/unit/test_telegram_parsing.py tests/contract/test_telegram_client.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/integrations tests/unit/test_telegram_parsing.py tests/contract/test_telegram_client.py
git commit -m "feat: add Telegram API integration"
```

## Task 5: Durable webhook и идемпотентная обработка updates

**Files:**
- Create: `app/services/updates.py`
- Create: `app/web/__init__.py`
- Create: `app/web/app.py`
- Create: `passenger_wsgi.py`
- Test: `tests/unit/test_update_service.py`
- Test: `tests/e2e/test_webhook_flow.py`

**Interfaces:**
- Produces: `UpdateService.accept(payload: dict, now: datetime) -> AcceptResult`
- Produces: `UpdateService.process(update_id: int, now: datetime) -> ProcessResult`
- Produces: WSGI callable `application(environ, start_response)`

- [ ] **Step 1: Написать падающий webhook test**

```python
def test_webhook_persists_before_processing(app, update_repository, processor) -> None:
    processor.raise_on_process = RuntimeError("interrupted")
    response = post_json(
        app,
        "/telegram/webhook",
        sample_update(update_id=101),
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )
    assert response.status_code == 200
    assert update_repository.get(101).status == UpdateStatus.RETRY


def test_duplicate_webhook_does_not_duplicate_processing(app, processor) -> None:
    payload = sample_update(update_id=102)
    post_json(app, "/telegram/webhook", payload, valid_secret_headers())
    post_json(app, "/telegram/webhook", payload, valid_secret_headers())
    assert processor.calls_for(102) == 1
```

- [ ] **Step 2: Запустить тест и подтвердить падение**

Run: `pytest tests/e2e/test_webhook_flow.py -v`

Expected: FAIL из-за отсутствия WSGI application.

- [ ] **Step 3: Реализовать accept/claim/process state machine**

```python
@dataclass(frozen=True)
class AcceptResult:
    update_id: int
    inserted: bool
    processed: bool


class UpdateService:
    def accept(self, payload: dict, now: datetime) -> AcceptResult:
        update_id = int(payload["update_id"])
        inserted = self.updates.enqueue(update_id, payload)
        if not inserted:
            return AcceptResult(update_id, False, False)
        try:
            self.process(update_id, now)
            return AcceptResult(update_id, True, True)
        except Exception as exc:
            self.updates.retry(update_id, now, now + timedelta(minutes=5), str(exc))
            return AcceptResult(update_id, True, False)
```

WSGI application:

- отвечает `405` не-POST запросу webhook;
- отвечает `403` неверному secret;
- отвечает `400` невалидному JSON;
- после durable enqueue отвечает `200 {"ok": true}`;
- предоставляет `GET /health`.

- [ ] **Step 4: Запустить unit и e2e tests**

Run: `pytest tests/unit/test_update_service.py tests/e2e/test_webhook_flow.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/updates.py app/web passenger_wsgi.py tests
git commit -m "feat: add durable Telegram webhook processing"
```

## Task 6: Групповые команды, права и сохранение сообщений

**Files:**
- Create: `app/services/access.py`
- Modify: `app/services/updates.py`
- Modify: `app/repositories/mysql.py`
- Test: `tests/unit/test_access_service.py`
- Test: `tests/e2e/test_group_commands.py`

**Interfaces:**
- Produces: `AccessService.enable_daily(chat_id, actor_id, title, now) -> None`
- Produces: `AccessService.disable_daily(chat_id, actor_id, now) -> None`
- Produces: `AccessService.verify_member(chat_id, user_id) -> bool`
- Update processor handles `/start`, `/enable_daily`, `/disable_daily`, `/summary`, text/caption

- [ ] **Step 1: Написать падающие тесты прав**

```python
def test_only_administrator_can_enable_group(access_service, telegram) -> None:
    telegram.member_status = "member"
    with pytest.raises(AccessDenied):
        access_service.enable_daily(-1001, 42, "Demo", fixed_now)

    telegram.member_status = "administrator"
    access_service.enable_daily(-1001, 42, "Demo", fixed_now)
    assert access_service.chats.is_enabled(-1001)


def test_tracked_disabled_chat_continues_buffering_messages(processor, repositories) -> None:
    repositories.chats.track(-1001, enabled=False)
    processor.process_payload(group_message_update(chat_id=-1001, message_id=7))
    assert repositories.messages.exists(-1001, 7)
```

- [ ] **Step 2: Запустить тесты и подтвердить падение**

Run: `pytest tests/unit/test_access_service.py tests/e2e/test_group_commands.py -v`

Expected: FAIL.

- [ ] **Step 3: Реализовать access policy и handlers**

Администраторы: `creator`, `administrator`.

Текущие участники для `/summary`: `creator`, `administrator`, `member`,
`restricted`.

Каждое увиденное сообщение обновляет `chat_members.last_seen_at`. Команда
`/summary` в группе устанавливает связь, необходимую для будущего личного
запроса.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/unit/test_access_service.py tests/e2e/test_group_commands.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services app/repositories/mysql.py tests
git commit -m "feat: add group access and message tracking"
```

## Task 7: Groq client, prompt validation и эвристический fallback

**Files:**
- Create: `app/integrations/groq.py`
- Create: `app/services/summaries.py`
- Copy/adapt: `mvp-1/summarizer.py` → focused helpers in `app/services/summaries.py`
- Test: `tests/contract/test_groq_client.py`
- Test: `tests/unit/test_summary_service.py`

**Interfaces:**
- Produces: `GroqClient.generate_summary(system_prompt, user_prompt) -> LlmResult`
- Produces: `SummaryService.generate(chat_id, kind, requester_user_id, now) -> GeneratedSummary`
- Produces: `build_source_hash(messages: Sequence[StoredMessage]) -> str`

- [ ] **Step 1: Написать падающие пороговые и fallback-тесты**

```python
def test_scheduled_summary_requires_15_messages(summary_service, messages, llm) -> None:
    messages.seed(14)
    result = summary_service.generate(-1001, SummaryKind.SCHEDULED, None, fixed_now)
    assert result.outcome == "insufficient_messages"
    assert llm.call_count == 0


def test_on_demand_uses_fallback_after_groq_timeout(summary_service, messages, llm) -> None:
    messages.seed(5)
    llm.error = LlmTransientError("timeout")
    result = summary_service.generate(-1001, SummaryKind.ON_DEMAND, 42, fixed_now)
    assert result.fallback_type == "heuristic"
    assert result.text
```

- [ ] **Step 2: Запустить тесты и подтвердить падение**

Run: `pytest tests/unit/test_summary_service.py tests/contract/test_groq_client.py -v`

Expected: FAIL.

- [ ] **Step 3: Реализовать Groq boundary**

Endpoint: `https://api.groq.com/openai/v1/chat/completions`.

Payload:

```python
{
    "model": self.model,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    "temperature": 0.3,
    "response_format": {"type": "json_object"},
}
```

Нормализовать `429`, timeout, `5xx`, malformed JSON и отсутствующий content.
Полный prompt и response не логировать.

- [ ] **Step 4: Реализовать summary service**

Алгоритм:

1. получить сообщения текущего дня;
2. проверить порог 15/5;
3. вычислить SHA-256 source hash из message IDs и text;
4. вернуть существующую валидную сводку по hash;
5. создать `preparing`;
6. вызвать Groq с bounded transcript;
7. проверить JSON, при необходимости сделать один repair;
8. при ошибке собрать эвристическую сводку;
9. сохранить валидный результат, не перетирая предыдущий пустым.

- [ ] **Step 5: Запустить tests**

Run: `pytest tests/unit/test_summary_service.py tests/contract/test_groq_client.py -v`

Expected: PASS, включая `429`, timeout, repair и fallback.

- [ ] **Step 6: Commit**

```bash
git add app/integrations/groq.py app/services/summaries.py tests
git commit -m "feat: add Groq summaries with deterministic fallback"
```

## Task 8: Групповой и личный `/summary`

**Files:**
- Modify: `app/services/updates.py`
- Modify: `app/services/access.py`
- Modify: `app/services/summaries.py`
- Modify: `app/repositories/mysql.py`
- Test: `tests/e2e/test_on_demand_summary.py`
- Test: `tests/e2e/test_private_summary.py`

**Interfaces:**
- Produces: callback action `summary_group:<opaque_selection_id>`
- Produces: active request deduplication by `(chat_id, source_hash)`

- [ ] **Step 1: Написать падающие e2e tests**

```python
def test_private_summary_for_one_group_is_private(system) -> None:
    system.link_user_to_group(user_id=42, chat_id=-1001)
    system.telegram.membership[-1001, 42] = "member"
    system.messages.seed(chat_id=-1001, count=5)
    system.receive(private_command(user_id=42, text="/summary"))
    assert system.telegram.last_message.chat_id == 42
    assert "сводк" in system.telegram.last_message.text.lower()


def test_private_summary_with_multiple_groups_shows_buttons(system) -> None:
    system.link_user_to_group(42, -1001)
    system.link_user_to_group(42, -1002)
    system.telegram.membership[-1001, 42] = "member"
    system.telegram.membership[-1002, 42] = "administrator"
    system.receive(private_command(user_id=42, text="/summary"))
    assert system.telegram.last_message.button_texts == ["Group 1", "Group 2"]
```

- [ ] **Step 2: Запустить tests и подтвердить падение**

Run: `pytest tests/e2e/test_on_demand_summary.py tests/e2e/test_private_summary.py -v`

Expected: FAIL.

- [ ] **Step 3: Реализовать acknowledgement, выбор и повторную проверку**

Правила:

- при LLM generation сначала отправить согласованный текст про 1–2 минуты;
- если generation уже активна, ответить, что сводка готовится;
- callback хранит opaque ID, не raw `chat_id`;
- непосредственно перед выдачей вызвать `getChatMember`;
- `left`/`kicked` удаляют группу из доступного выбора;
- результат группового запроса отправляется в группу, личного — пользователю.

- [ ] **Step 4: Запустить e2e tests**

Run: `pytest tests/e2e/test_on_demand_summary.py tests/e2e/test_private_summary.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app tests/e2e
git commit -m "feat: add group and private summary requests"
```

## Task 9: Плановая публикация, archive lifecycle и безопасная очистка

**Files:**
- Create: `app/services/lifecycle.py`
- Create: `app/jobs/__init__.py`
- Create: `app/jobs/cli.py`
- Modify: `app/repositories/mysql.py`
- Test: `tests/unit/test_lifecycle_service.py`
- Test: `tests/integration/test_archive_cleanup.py`
- Test: `tests/e2e/test_scheduled_publication.py`

**Interfaces:**
- Produces: `LifecycleService.prepare_day(now) -> JobReport`
- Produces: `LifecycleService.publish_day(now) -> JobReport`
- Produces: `LifecycleService.archive_day(now) -> JobReport`
- Produces: `LifecycleService.cleanup_day(now) -> JobReport`
- Produces CLI commands `recover`, `prepare`, `publish`, `archive`, `cleanup`, `purge`, `audit`

- [ ] **Step 1: Написать падающие safety tests**

```python
def test_repeated_publish_sends_once(system) -> None:
    system.prepare_scheduled_summary(chat_id=-1001, date="2026-07-16")
    system.run_job("publish")
    system.run_job("publish")
    assert system.telegram.sent_count(chat_id=-1001) == 1


def test_cleanup_refuses_unverified_archive(lifecycle, repositories) -> None:
    repositories.lifecycle.record_day(-1001, "2026-07-16", archive_verified=False)
    report = lifecycle.cleanup_day(fixed_now)
    assert report.deleted_messages == 0
    assert repositories.messages.count_for_chat(-1001) > 0
```

- [ ] **Step 2: Запустить tests и подтвердить падение**

Run: `pytest tests/unit/test_lifecycle_service.py tests/e2e/test_scheduled_publication.py -v`

Expected: FAIL.

- [ ] **Step 3: Реализовать outbox и daily lifecycle**

Archive transaction:

```python
with database.transaction() as tx:
    expected_ids = messages.ids_for_day(chat_id, day, tx=tx)
    archive.copy_messages(chat_id, day, tx=tx)
    archived_ids = archive.ids_for_day(chat_id, day, tx=tx)
    if expected_ids != archived_ids:
        raise ArchiveVerificationError(chat_id, day)
    lifecycle.mark_archive_completed(chat_id, day, len(expected_ids), tx=tx)
```

Cleanup в отдельной транзакции проверяет `archive_completed_at` и verification.
Для дня ниже порога требуется явно записанный outcome
`automatic_summary_skipped`.

- [ ] **Step 4: Реализовать CLI и recovery leases**

Run format:

```bash
python -m app.jobs.cli recover
python -m app.jobs.cli prepare
python -m app.jobs.cli publish
python -m app.jobs.cli archive
python -m app.jobs.cli cleanup
python -m app.jobs.cli purge
```

Каждая команда получает MySQL lease с expiry и возвращает exit code `0` при
успехе, `1` при полной ошибке, `2` при неверной конфигурации.

- [ ] **Step 5: Запустить tests**

Run:

```bash
pytest tests/unit/test_lifecycle_service.py \
  tests/integration/test_archive_cleanup.py \
  tests/e2e/test_scheduled_publication.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/lifecycle.py app/jobs app/repositories/mysql.py tests
git commit -m "feat: add scheduled publication and safe daily lifecycle"
```

## Task 10: Retention, backup и operational audit

**Files:**
- Create: `app/jobs/audit.py`
- Create: `scripts/backup_db.sh`
- Modify: `app/jobs/cli.py`
- Create: `logs.README.md`
- Test: `tests/unit/test_retention.py`
- Test: `tests/unit/test_audit.py`

**Interfaces:**
- Produces: `build_audit_report(now) -> AuditReport`
- Produces: `purge_expired(now, retention_days=30) -> PurgeReport`

- [ ] **Step 1: Написать падающие retention tests**

```python
def test_purge_uses_strict_30_day_cutoff(repository) -> None:
    now = datetime(2026, 7, 16, 3, 15, tzinfo=ZoneInfo("Europe/Moscow"))
    repository.seed(created_at=now - timedelta(days=30), key="boundary")
    repository.seed(created_at=now - timedelta(days=30, seconds=1), key="old")
    report = purge_expired(repository, now, retention_days=30)
    assert report.deleted_keys == ["old"]
    assert repository.exists("boundary")
```

- [ ] **Step 2: Запустить tests и подтвердить падение**

Run: `pytest tests/unit/test_retention.py tests/unit/test_audit.py -v`

Expected: FAIL.

- [ ] **Step 3: Реализовать purge и audit**

Audit проверяет pending/retry age, stale leases, unsent scheduled summaries,
archive failures, latest backup metadata и Groq fallback rate. Если есть
actionable issues и настроен `OWNER_CHAT_ID`, отправляется одно краткое
сообщение без пользовательских текстов.

- [ ] **Step 4: Реализовать backup script**

`scripts/backup_db.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
. ./.env
set +a
mkdir -p backups logs
stamp="$(date +%Y%m%d-%H%M%S)"
target="backups/brevity-${stamp}.sql.gz"
mysqldump --single-transaction --quick --skip-lock-tables \
  -h "$DB_HOST" -u "$DB_USER" "-p$DB_PASSWORD" "$DB_NAME" | gzip > "$target"
find backups -type f -name 'brevity-*.sql.gz' -mtime +30 -delete
test -s "$target"
```

- [ ] **Step 5: Запустить tests и shell syntax check**

Run:

```bash
pytest tests/unit/test_retention.py tests/unit/test_audit.py -v
bash -n scripts/backup_db.sh
```

Expected: PASS и exit `0`.

- [ ] **Step 6: Commit**

```bash
git add app/jobs scripts/backup_db.sh logs.README.md tests/unit
git commit -m "feat: add retention backups and operational audit"
```

## Task 11: Одноразовый импорт MVP

**Files:**
- Create: `scripts/import_mvp.py`
- Test: `tests/integration/test_import_mvp.py`
- Create: `docs/MVP_IMPORT_RUNBOOK.md`

**Interfaces:**
- Produces: `import_mvp(bot_db: Path, archive_db: Path, repositories) -> ImportReport`
- Produces JSON report with source counts, imported counts, duplicate counts and mismatches

- [ ] **Step 1: Написать падающий import test**

```python
def test_import_is_idempotent_and_preserves_source(tmp_path, mysql_repositories) -> None:
    bot_db, archive_db = build_legacy_fixture(tmp_path)
    before = (sha256_file(bot_db), sha256_file(archive_db))
    first = import_mvp(bot_db, archive_db, mysql_repositories)
    second = import_mvp(bot_db, archive_db, mysql_repositories)
    assert first.mismatches == []
    assert second.inserted_messages == 0
    assert before == (sha256_file(bot_db), sha256_file(archive_db))
```

- [ ] **Step 2: Запустить test и подтвердить падение**

Run: `pytest tests/integration/test_import_mvp.py -v`

Expected: FAIL.

- [ ] **Step 3: Реализовать read-only import**

CLI:

```bash
python scripts/import_mvp.py \
  --bot-db /secure/path/bot.db \
  --archive-db /secure/path/archive.db \
  --report /secure/path/import-report.json
```

До чтения создаются immutable filesystem copies. Импорт использует уникальные
ключи новой схемы и сравнивает source IDs с destination IDs. Любое mismatch
завершает команду exit code `1`; SQLite sources не изменяются.

- [ ] **Step 4: Запустить integration test**

Run: `pytest tests/integration/test_import_mvp.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/import_mvp.py tests/integration/test_import_mvp.py docs/MVP_IMPORT_RUNBOOK.md
git commit -m "feat: add verified MVP data import"
```

## Task 12: Beget deploy, preview и webhook tooling

**Files:**
- Create: `scripts/deploy_beget.exp`
- Create: `scripts/set_webhook.py`
- Create: `scripts/get_webhook_info.py`
- Create: `scripts/smoke_preview.py`
- Create: `hosting.env.example`
- Create: `docs/BEGET_DEPLOYMENT.md`
- Create: `docs/PREVIEW_ENVIRONMENT.md`
- Test: `tests/unit/test_deploy_excludes.py`

**Interfaces:**
- Produces safe rsync exclude list
- Produces webhook scripts using `WEBHOOK_URL` and `WEBHOOK_SECRET_TOKEN`
- Produces preview smoke command with non-production database assertion

- [ ] **Step 1: Написать падающий deploy safety test**

```python
def test_deploy_excludes_runtime_and_legacy_data() -> None:
    excludes = load_deploy_excludes(Path("scripts/deploy_beget.exp"))
    assert {
        ".git", ".venv", ".env", ".env.hosting", ".superpowers",
        "backups", "logs", "bot.db", "archive.db", "mvp-1/bot.db",
        "mvp-1/archive.db",
    } <= excludes
```

- [ ] **Step 2: Запустить test и подтвердить падение**

Run: `pytest tests/unit/test_deploy_excludes.py -v`

Expected: FAIL.

- [ ] **Step 3: Реализовать deploy и Passenger layout**

Deploy:

- читает `.env.hosting`;
- делает production backup перед миграцией;
- rsync не использует destructive database operations;
- копирует `passenger_wsgi.py`;
- устанавливает dependencies в project-local `.deps` или venv;
- выполняет migration;
- касается `tmp/restart.txt`;
- запускает health check.

Preview использует отдельные:

```text
TELEGRAM_BOT_TOKEN
WEBHOOK_SECRET_TOKEN
WEBHOOK_URL
DB_NAME
DB_USER
DB_PASSWORD
BEGET_REMOTE_DIR
```

- [ ] **Step 4: Реализовать webhook и smoke scripts**

Smoke проверяет:

1. `/health`;
2. invalid secret → `403`;
3. valid webhook fixture → `200`;
4. preview DB marker отличается от production marker;
5. getWebhookInfo указывает preview URL;
6. backup существует вне `public_html`.

- [ ] **Step 5: Запустить tests и syntax checks**

Run:

```bash
pytest tests/unit/test_deploy_excludes.py -v
python -m py_compile scripts/set_webhook.py scripts/get_webhook_info.py scripts/smoke_preview.py
expect -n scripts/deploy_beget.exp
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts hosting.env.example docs tests/unit/test_deploy_excludes.py
git commit -m "feat: add Beget preview and deployment tooling"
```

## Task 13: Полная verification и релиз `0.2.0`

**Files:**
- Modify: `README.md`
- Create: `CHANGELOG.md`
- Create: `docs/PRODUCTION_CUTOVER.md`
- Modify: `.env.example`
- Modify: `pyproject.toml` only if dependency verification requires exact corrections

**Interfaces:**
- Produces documented local, preview, production, rollback and recovery commands
- Produces release candidate ready for tag `v0.2.0`

- [ ] **Step 1: Обновить README и changelog**

README должен содержать:

- назначение `mvp-1/`;
- архитектуру webhook/MySQL/recovery;
- локальную установку;
- required environment;
- команды migration и tests;
- команды jobs;
- ссылку на русскую и английскую спеки;
- ссылку на deployment и cutover runbooks.

`CHANGELOG.md`:

```markdown
## [0.2.0] - 2026-07-16

### Added
- Durable Telegram webhook and MySQL storage.
- Scheduled and private Groq summaries with heuristic fallback.
- Verified 30-day archive lifecycle, recovery jobs, backups and preview deployment.

### Changed
- Preserved the cron/SQLite/Ollama MVP under `mvp-1/`.
```

- [ ] **Step 2: Запустить полный automated suite**

Run:

```bash
pytest -v
ruff check app scripts tests
python -m compileall -q app scripts
python3 -m unittest discover -s mvp-1/tests -v
```

Expected: все tests PASS, ruff exit `0`, compileall exit `0`, MVP tests PASS.

- [ ] **Step 3: Проверить миграцию и импорт на disposable MySQL**

Run:

```bash
python scripts/migrate.py
python scripts/import_mvp.py \
  --bot-db mvp-1/bot.db.backup \
  --archive-db mvp-1/archive.db.backup \
  --report /tmp/brevity-import-report.json
pytest tests/integration -v
```

Expected: migrations applied once; second run no-op; import report has
`"mismatches": []`; integration tests PASS.

- [ ] **Step 4: Развернуть preview и выполнить smoke checklist**

Run:

```bash
scripts/deploy_beget.exp --environment preview
python scripts/set_webhook.py --environment preview
python scripts/smoke_preview.py
```

Expected: все smoke checks PASS; production DB row counts unchanged.

- [ ] **Step 5: Провести production cutover**

Выполнить строго по `docs/PRODUCTION_CUTOVER.md`:

1. создать backup SQLite и MySQL;
2. остановить old polling/cron collection;
3. deploy production;
4. migrate;
5. import and verify;
6. set webhook;
7. verify live group update;
8. оставить cleanup disabled до успешного полного дневного цикла.

- [ ] **Step 6: Проверить production recovery и дневной цикл**

Run вручную из Beget:

```bash
python -m app.jobs.cli recover
python -m app.jobs.cli prepare
python -m app.jobs.cli publish
python -m app.jobs.cli archive
python -m app.jobs.cli cleanup --dry-run
python -m app.jobs.cli audit
```

Expected: повторные запуски идемпотентны; publish не дублируется; archive
verified; cleanup dry-run перечисляет только разрешённые строки.

- [ ] **Step 7: Создать release commit и tag**

```bash
git add README.md CHANGELOG.md docs .env.example pyproject.toml
git commit -m "release: BrevitySisterBot 0.2.0"
git tag -a v0.2.0 -m "BrevitySisterBot 0.2.0"
```

## Контрольные точки выполнения

После Tasks 1–3: review структуры репозитория и MySQL-схемы.

После Tasks 4–6: review durable webhook, идемпотентности и access policy.

После Tasks 7–9: review качества сводки, личного доступа и защиты архива.

После Tasks 10–12: review эксплуатации, импорта и Beget preview.

После Task 13: production cutover разрешается только после отдельного
подтверждения пользователя.
