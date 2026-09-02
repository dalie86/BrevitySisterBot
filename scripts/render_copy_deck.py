from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.content import BotCopy  # noqa: E402

COPY_PATH = PROJECT_ROOT / "content" / "bot_copy.ru.toml"
DECK_PATH = PROJECT_ROOT / "docs" / "BOT_COPY_RU.md"
SECTION_ORDER = (
    "messages",
    "buttons",
    "summary",
    "fallback",
    "prompts",
    "usage_report",
)

CONTEXTS = {
    ("messages", "group_commands"): "Пользователь отправляет /start в группе.",
    ("messages", "daily_enabled"): "Администратор успешно отправил /enable_daily.",
    ("messages", "daily_disabled"): "Администратор успешно отправил /disable_daily.",
    (
        "messages",
        "group_not_connected",
    ): "Участник вызывает /summary в неподключённой группе.",
    (
        "messages",
        "admin_required",
    ): "Не-администратор меняет настройку ежедневной сводки.",
    ("messages", "summary_accepted"): "Ручной запрос принят до обращения к модели.",
    (
        "messages",
        "summary_in_progress",
    ): "Тот же набор сообщений уже обрабатывается.",
    (
        "messages",
        "not_enough_messages",
    ): "Для ручной сводки накоплено меньше пяти сообщений.",
    (
        "messages",
        "choose_group_native",
    ): "В личном чате нет запомненной доступной группы.",
    ("messages", "choose_group_known"): "В личном чате доступно несколько групп.",
    (
        "messages",
        "group_choice_expired",
    ): "Одночасовой выбор группы недействителен.",
    (
        "messages",
        "membership_not_confirmed",
    ): "Telegram не подтверждает членство перед выдачей.",
    ("messages", "bot_admin_required"): "Бот не администратор при /enable_daily.",
    (
        "messages",
        "private_link_invalid",
    ): "Групповая deep link-ссылка неизвестна или повреждена.",
    (
        "messages",
        "selected_group_not_connected",
    ): "Выбранная через Telegram группа не подключена.",
    (
        "messages",
        "group_access_unverifiable",
    ): "Бот не может надёжно проверить выбранную группу.",
    (
        "buttons",
        "get_private_summary",
    ): "Кнопка под успешным ответом на /enable_daily.",
    ("buttons", "choose_group"): "Нативная reply-кнопка Telegram request_chat.",
    (
        "summary",
        "document",
    ): "Внешняя модель вернула валидный JSON; количество тем — от нуля до четырёх.",
    ("summary", "topic"): "Повторяющийся блок одной темы.",
    ("summary", "topics_separator"): "Разделитель блоков тем.",
    (
        "fallback",
        "document",
    ): "DeepSeek недоступен, вернул ошибку или невалидный ответ; бот составляет детерминированную локальную сводку без интерпретации содержания.",
    (
        "fallback",
        "participants_unknown",
    ): "Подстановка, когда участники не определены.",
    ("fallback", "message_item"): "Строка одного сообщения в локальной сводке.",
    (
        "fallback",
        "messages_separator",
    ): "Разделитель строк локальной сводки.",
    ("prompts", "system"): "Системная инструкция суммаризатору.",
    (
        "prompts",
        "user",
    ): "Запрос с сегодняшней перепиской (последние 18 000 символов).",
    ("prompts", "transcript_line"): "Формат одной строки переписки.",
    ("prompts", "repair"): "Одна попытка исправления; первые 6000 символов ответа.",
    (
        "usage_report",
        "weekly_base",
    ): "Служебный еженедельный отчёт владельцу без данных групп и участников.",
    (
        "usage_report",
        "insufficient_balance",
    ): "DeepSeek ответил HTTP 402 при проверке баланса.",
    (
        "usage_report",
        "previous_unavailable",
    ): "Предыдущая недельная проверка баланса была недоступна.",
}

PREAMBLE = """Этот файл генерируется из `content/bot_copy.ru.toml`. Не редактируйте
его вручную: измените TOML и снова запустите renderer.

## Контекст продукта и ограничения

- Бот делает сводки переписки подключённых Telegram-групп за текущий день.
- Автоматическая сводка требует минимум 15 сообщений и публикуется в 21:00 по
  Москве; ручная сводка по `/summary` требует минимум 5 сообщений.
- Ручной запрос работает и в группе, и напрямую в личном чате. Групповой
  результат видят все участники, личный — только запросивший пользователь.
- Выбор через нативный интерфейс Telegram или групповую deep link-ссылку проверяет доступ, после успеха запоминает группу и сразу начинает готовить сегодняшнюю личную сводку.
- Предварительно отправлять `/summary` в группе не требуется.
- Одна запомненная доступная группа запускает подготовку сразу; если их несколько, бот предлагает выбор.
- Подключать и отключать ежедневную публикацию может только администратор
  группы. Сам бот тоже должен быть администратором подключённой группы.
- После подключения бот публикует кнопку личного получения, но не закрепляет
  сообщение автоматически.
- Отключение ежедневной публикации не останавливает накопление сообщений и не
  отключает ручные сводки.
- Перед личной выдачей Telegram должен подтвердить членство пользователя.
- Подготовка может занимать до 2 минут; повторный запрос не ускоряет результат.
- Итог должен помещаться в одно Telegram-сообщение: максимум 3900 символов.
- Модель пишет только по фактам переписки, без домыслов, оценок и выдуманных
  решений, эмоций, консенсуса или важности темы.
- Команды, ключи TOML и переменные вида `{{variable}}` менять нельзя без
  соответствующего изменения интерфейса и кода.
- Тон: спокойный, понятный, человечный и лаконичный — без канцелярита,
  давления, фамильярности и лишних технических подробностей.

## Молчаливые сценарии

Бот намеренно ничего не отвечает:

- на неизвестную команду в группе;
- на обычное сообщение в личном чате;
- на обычный `/start` в личном чате без групповой deep link-ссылки;
- на обычное сообщение в неподключённой группе;
- после успешного сохранения обычного группового сообщения;
- когда автоматическая сводка пропущена из-за порога менее 15 сообщений.

Добавление ответа в любой из этих сценариев меняет поведение продукта и требует
отдельного решения, а не только редакторской правки.

## Готовый запрос для внешнего редактора

Скопируйте следующий запрос вместе с этим документом и исходным TOML:

```text
Ты UX-редактор Telegram-бота. Используй приложенный контекст показа и полный
TOML как единый источник текстов. Перепиши пользовательские сообщения,
шаблоны и LLM-промпты на естественном современном русском. Сохрани все факты,
команды, числа, ограничения, условия показа, названия секций и ключей, а также
все переменные вида {{variable}}. Сделай тон спокойным, дружелюбным и
лаконичным. Не добавляй функциональность. Улучши основной и резервный шаблоны,
а также промпты так, чтобы результат оставался фактическим, компактным и не
приписывал переписке отсутствующие решения, эмоции, консенсус или важность.
Верни полный валидный TOML без сокращений и без Markdown-обёртки. Комментарии
редактора помести после TOML отдельным списком.
```
"""


def render(copy: BotCopy) -> str:
    sections = ["# BrevitySisterBot 0.3.0 — вординг и промпты", "", PREAMBLE]
    for section in SECTION_ORDER:
        sections.extend((f"## `{section}`", ""))
        for key, value in copy.sections[section].items():
            sections.extend((f"### `{section}.{key}`", ""))
            context = CONTEXTS.get((section, key))
            if context:
                sections.extend((f"Контекст: {context}", ""))
            sections.extend(("```text", value, "```", ""))
    return "\n".join(sections).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the Russian bot copy deck")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if docs/BOT_COPY_RU.md differs from the generated deck",
    )
    args = parser.parse_args(argv)

    generated = render(BotCopy.load(COPY_PATH)).encode("utf-8")
    if args.check:
        try:
            current = DECK_PATH.read_bytes()
        except OSError as exc:
            print(f"Copy deck is unavailable: {exc}", file=sys.stderr)
            return 1
        if current != generated:
            print(
                "docs/BOT_COPY_RU.md is out of date; run "
                ".venv/bin/python scripts/render_copy_deck.py",
                file=sys.stderr,
            )
            return 1
        return 0

    DECK_PATH.write_bytes(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
