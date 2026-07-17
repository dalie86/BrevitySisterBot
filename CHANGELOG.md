# Changelog

## 0.2.0 — 2026-07-16

- Полностью переписан production-код; версия `0.1.0` сохранена в `mvp-1/`.
- Добавлены Python WSGI webhook, durable MySQL queue и пятиминутный recovery.
- Добавлены групповые права, сбор сообщений и защита от дублей.
- Добавлена суммаризация через Groq/Qwen с проверкой JSON и локальным fallback.
- Добавлены групповые и приватные `/summary` с повторной проверкой членства.
- Добавлены подготовка в 20:45 и публикация в 21:00.
- Добавлены проверяемый архив, безопасная очистка и 30-дневный retention.
- Добавлены MySQL backup, operational audit и read-only импорт MVP.
- Добавлены Passenger layout и deploy tooling для
  `brevity.prosto-kosmos.ru`.
