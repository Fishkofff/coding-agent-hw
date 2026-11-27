# Учебный маркет скиллов

- `index-v1.json` — первая версия каталога: sprint-report 1.0.0, workload-summary 1.0.0.
- `index-v2.json` — вторая версия: sprint-report обновлён до 1.1.0, добавлен standup-notes, broken-example с неверным sha256.
- `index.json` — рабочий каталог, на который смотрит policy (`market.sources.study-market.index`).
- `packages/<name>/<version>/SKILL.md` — пакеты; в каталоге для каждого записан sha256 файла SKILL.md.

Демо смены версии маркета: `cp market/index-v2.json market/index.json`.
