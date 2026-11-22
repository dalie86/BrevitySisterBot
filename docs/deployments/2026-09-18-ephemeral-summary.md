# Выкладка скрытых ручных сводок — 2026-09-18

- Сервер: Beget, `~/brevity.prosto-kosmos.ru`.
- База релиза: 0.3.0, commit `6ac0bac`, Python 3.10.12.
- Адаптированный исходный код: `.worktrees/private-summary-implementation`.
- Первоначальная правка в корне проекта подготовлена на 0.2.0; на сервер
  перенесена адаптация к 0.3.0, без отката DeepSeek и личного onboarding.
- Проверки до выкладки: 306 pytest tests passed, Ruff passed; независимое
  ревью не выявило существенных проблем.
- До замены проверено совпадение SHA-256 каждого заменяемого файла с базой.
- Резервная копия базы и прежних файлов:
  `backups/ephemeral-20260918T055500Z/` на сервере.
  SQL backup проверен на наличие таблиц и отметки завершения mysqldump.
- Обновлены 7 файлов: Telegram port/client, durable gateway, group handler,
  on-demand service, TOML menu copy, scripts/set_commands.py.
- После замены проверены импорты production-приложения и контрольные суммы;
  запрошен перезапуск Passenger через tmp/restart.txt; /health вернул ok=true.
- Telegram setMyCommands выполнен, getMyCommands подтвердил is_ephemeral=true
  для /summary в default/all_group_chats/all_chat_administrators и false в
  all_private_chats, для языков default и ru. Всего проверено 8 меню.
- Сначала была временная сетевая ошибка Telegram; повтор с ограниченными
  повторами завершился успешно. Старые меню сохранены в резервной копии.
- Pending webhook updates: 0. Обе подключённые группы: бот администратор,
  enabled=true, timezone=Europe/Moscow, scheduled_publish_time=21:00:00.
- Тестовые сообщения пользователям не отправлялись; визуальная проверка
  скрытого ответа на устройстве не выполнялась.
