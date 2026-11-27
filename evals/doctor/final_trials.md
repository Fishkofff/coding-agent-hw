# Доктор: разбор реальных сессий финального A/B eval

Источник: журналы событий 30 trials `evals/results/final/trials/*/*/runs/sessions/*/events.jsonl`
(модель openai/gpt-4.1-mini через ProxyAPI, usage полный во всех run). Команда для одной сессии:
`python3 -m agent doctor <session_id> --policy evals/results/final/trials/<case>/<trial>/policy.json`.

Правила заданы заранее (`agent/doctor.py → RULES`): `repeated_error`, `unchanged_reread`, `skill_without_actions`, `finished_without_verification`.

## Сводка находок по всем 30 сессиям

| trial | находки | outcome_pass | run_id |
|---|---|---|---|
| c1_open_tasks_mcp A1 | finished_without_verification | False | 20261127-212144-51bab7 |
| c1_open_tasks_mcp A2 | finished_without_verification | False | 20261127-212155-c86dd8 |
| c1_open_tasks_mcp A3 | finished_without_verification | False | 20261127-212205-a23b70 |
| c1_open_tasks_mcp B1 | - | True | 20261127-212149-411b05 |
| c1_open_tasks_mcp B2 | - | True | 20261127-212158-bedc33 |
| c1_open_tasks_mcp B3 | - | True | 20261127-212209-5bea38 |
| c2_sprint_report A1 | finished_without_verification | True | 20261127-212215-797ab9 |
| c2_sprint_report A2 | finished_without_verification | True | 20261127-212228-aa935d |
| c2_sprint_report A3 | finished_without_verification | True | 20261127-212240-0cee28 |
| c2_sprint_report B1 | finished_without_verification | False | 20261127-212221-fe0c8f |
| c2_sprint_report B2 | finished_without_verification | False | 20261127-212234-cc0c84 |
| c2_sprint_report B3 | finished_without_verification | False | 20261127-212247-90672b |
| c3_fix_order_no_skill A1 | - | True | 20261127-212254-037341 |
| c3_fix_order_no_skill A2 | - | True | 20261127-212312-1ce97b |
| c3_fix_order_no_skill A3 | - | True | 20261127-212327-dcfed0 |
| c3_fix_order_no_skill B1 | - | True | 20261127-212303-2ce7e4 |
| c3_fix_order_no_skill B2 | - | True | 20261127-212319-7f5d8a |
| c3_fix_order_no_skill B3 | - | True | 20261127-212334-b3b33c |
| c4_incomplete_report A1 | finished_without_verification | False | 20261127-212342-73cdfa |
| c4_incomplete_report A2 | finished_without_verification | False | 20261127-212402-a0f815 |
| c4_incomplete_report A3 | finished_without_verification | False | 20261127-212423-655f39 |
| c4_incomplete_report B1 | finished_without_verification | False | 20261127-212352-b3c414 |
| c4_incomplete_report B2 | - | True | 20261127-212411-eaa18a |
| c4_incomplete_report B3 | - | True | 20261127-212433-2e7b0e |
| c5_mcp_failure A1 | - | True | 20261127-212442-4ea866 |
| c5_mcp_failure A2 | - | True | 20261127-212448-76673c |
| c5_mcp_failure A3 | - | True | 20261127-212455-135757 |
| c5_mcp_failure B1 | - | True | 20261127-212444-c8dcab |
| c5_mcp_failure B2 | - | True | 20261127-212451-2553c7 |
| c5_mcp_failure B3 | - | True | 20261127-212458-860a8b |

Находок `finished_without_verification`: 13 из 30 сессий. Из 13 отмеченных trials провалили проверку результата 10; все 10 провалившихся trials отмечены этим правилом, а среди неотмеченных провалов нет.
Интерпретация: правило не доказывает ошибку (c2 A прошёл все 3 раза без перепроверки), но на этом наборе завершение без проверки записанного файла — сильный признак риска. Остальные правила (`repeated_error`, `unchanged_reread`, `skill_without_actions`) в финальных сессиях не сработали: повторных ошибок и лишних чтений не было, а после каждой загрузки скилла шли действия. Их срабатывание на проблемных примерах и отсутствие на похожих нормальных проверено в `tests/test_doctor.py`.
Предложение: в B провалы c2/c4 случились, когда модель не загрузила `sprint-report`, где есть шаг перечитывания отчёта. Шаг проверки результата стоит вынести из скилла в общие правила системного промпта («после записи файла перечитай его и сверь с данными»), а описание `sprint-report` сделать ближе к словам запроса («отчёт о состоянии спринта»).

## Пример полного отчёта доктора: c2_sprint_report B1

```
Доктор сессии (правила, без LLM: собственный расход 0 запросов к модели)

Расходы по запускам:
  20261127-212221-fe0c8f: Запросов к модели: 3; input tokens: 4989; output tokens: 369 (из них cached input: 3968); оценка input по символам: ~6241
    вызовы по категориям: {'builtin': 3}; байты результатов: {'builtin': 1333}
Частота скиллов: -
MCP-вызовы: -

Находка 1: finished_without_verification — run завершён, файл записан, но после последней записи не было ни read_file этого файла, ни Bash
  run_id: 20261127-212221-fe0c8f
  события: ev-2ec6e696b558, ev-aff450173b95, ev-1c52c1a45f51
  счётчики: {"writes": 1, "checks_after_last_write": 0}
  интерпретация: последняя запись report.md не была проверена: после неё нет read_file этого файла и нет Bash; итоговый ответ опирается только на намерение модели
  предложение: добавить в скилл шаг «перечитай записанный файл и сверь с данными» или запуск теста/проверки
```
