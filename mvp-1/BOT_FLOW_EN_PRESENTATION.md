# Telegram Daily Summary Bot

## Presentation Diagram

```mermaid
flowchart LR
    classDef source fill:#FFF4CC,stroke:#C89B00,color:#3A2E00,stroke-width:1.5px;
    classDef process fill:#DCEBFF,stroke:#2F6DB5,color:#12304F,stroke-width:1.5px;
    classDef ai fill:#FDE1E1,stroke:#C44E52,color:#5A1F22,stroke-width:1.5px;
    classDef store fill:#E3F6E8,stroke:#3A8F5E,color:#1D4C31,stroke-width:1.5px;
    classDef output fill:#EFE3FF,stroke:#7A4CC2,color:#32185E,stroke-width:1.5px;

    TG["Telegram Group"]:::source

    subgraph INGEST["1. Message Ingestion"]
        COLLECT["collect job<br/>hourly from 10:00 to 20:00<br/>+ final collect at 20:40"]:::process
        WORKDB["bot.db<br/>working database"]:::store
    end

    subgraph SUMMARY["2. Summary Preparation"]
        PREPARE["prepare job<br/>20:45 MSK"]:::process
        SUMM["summarizer.py<br/>validation + fallback + repair"]:::process
        LLM["LLM provider<br/>Ollama / Qwen<br/>or Yandex / GigaChat"]:::ai
        PREPARED["prepared_summaries<br/>inside bot.db"]:::store
    end

    subgraph DELIVERY["3. Delivery"]
        SEND["send job<br/>21:00 MSK"]:::process
    end

    subgraph HISTORY["4. Archive & Cleanup"]
        ARCHIVE["archive job<br/>21:10 MSK"]:::process
        ARCHDB["archive.db<br/>history storage"]:::store
        CLEAN["cleanup job<br/>21:15 MSK"]:::process
    end

    TG -->|"new messages / commands"| COLLECT
    COLLECT -->|"getUpdates + persist messages"| WORKDB

    WORKDB --> PREPARE
    PREPARE --> SUMM
    SUMM -->|"structured prompt"| LLM
    LLM -->|"raw model response"| SUMM
    SUMM -->|"validated summary"| PREPARED

    PREPARED --> SEND
    SEND -->|"sendMessage"| TG

    WORKDB --> ARCHIVE
    PREPARED --> ARCHIVE
    ARCHIVE --> ARCHDB

    WORKDB --> CLEAN
    PREPARED --> CLEAN
    CLEAN -->|"remove today's working data"| WORKDB
```

## Executive Summary

- The bot is not a permanent online service; it is a scheduled workflow driven by cron jobs.
- `bot.db` is the live working database for the current day.
- `archive.db` is the long-term history database.
- The summary pipeline includes LLM validation, repair, and fallback logic before publication.
- Daily data is archived after publishing and then removed from the working database.

## Daily Schedule

```mermaid
timeline
    title Daily Bot Timeline (MSK)
    10:00-20:00 : hourly collect jobs
    20:40 : final collect
    20:45 : prepare summary via LLM
    21:00 : publish summary to Telegram
    21:10 : archive today's messages and summary
    21:15 : clean today's data from bot.db
```

## Reading Guide

- Yellow = Telegram input
- Blue = scheduled processing stages
- Red = LLM interaction
- Green = data storage
- Purple = message delivery back to Telegram
