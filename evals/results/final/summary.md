# Сводка eval: hw2-skills-ab

**Гипотеза.** Скиллы помогают маленькой модели соблюдать соглашения задачи, которых нет в запросе: какие задачи считать открытыми, обязательные разделы отчёта, отказ от выдуманных данных при сбое источника. Ожидаем, что B проходит не меньше сценариев, чем A, и чаще проходит c1, c4 и c5; расход токенов у B выше из-за загрузки SKILL.md.

**Критерии.** outcome_pass — только программная проверка результата (файлы, тесты, итоговый ответ). routing_pass — для B: загружены ожидаемые скиллы и не загружены лишние; для A n/a. tools_pass — вызваны обязательные инструменты.

Режим: auto; подтверждения в eval: deny; бюджет на trial: {"model_calls": 20, "tool_calls": 40, "side_effects": 10, "result_bytes": 200000}; trials на версию: 3. Строк результатов: 30.
Модель: {"kind": "openai-compatible", "base_url": "https://openai.api.proxyapi.ru/v1", "model": "openai/gpt-4.1-mini", "temperature": 0.0, "no_think": false}

## Итог по сценариям

| сценарий | версия | outcome pass | routing ошибок | tools pass | ср. input | ср. output | usage | ср. MCP-вызовов | ср. вызовов | ошибки |
|---|---|---|---|---|---|---|---|---|---|---|
| c1_open_tasks_mcp | A | 0/3 | n/a | 3/3 | 3381 | 81 | complete | 1 | 2 | 0 |
| c1_open_tasks_mcp | B | 3/3 | 0 | 3/3 | 9661 | 126 | complete | 1 | 4 | 0 |
| c2_sprint_report | A | 3/3 | n/a | 3/3 | 3889 | 319 | complete | 0 | 3 | 0 |
| c2_sprint_report | B | 0/3 | 3 | 3/3 | 4961 | 342 | complete | 0 | 3 | 0 |
| c3_fix_order_no_skill | A | 3/3 | n/a | 3/3 | 5872 | 264 | complete | 0 | 5 | 0 |
| c3_fix_order_no_skill | B | 3/3 | 0 | 3/3 | 7693 | 302 | complete | 0 | 5 | 0 |
| c4_incomplete_report | A | 0/3 | n/a | 3/3 | 5246 | 561 | complete | 0 | 4 | 0 |
| c4_incomplete_report | B | 2/3 | 1 | 3/3 | 9638 | 482 | complete | 0 | 5 | 0 |
| c5_mcp_failure | A | 3/3 | n/a | 3/3 | 1857 | 67 | complete | 1 | 1 | 0 |
| c5_mcp_failure | B | 3/3 | 0 | 3/3 | 4402 | 66 | complete | 1 | 2 | 0 |

## Итог по версиям

- A: успешных 9/15, ср. input 4049, ср. output 258, ошибок выбора скиллов 0
- B: успешных 11/15, ср. input 7271, ср. output 263, ошибок выбора скиллов 4

## Все trials

| case_id | A/B | trial | run_id | outcome_pass | routing_pass_or_na | input_tokens | output_tokens | skill_activations | mcp_calls | error | журнал |
|---|---|---|---|---|---|---|---|---|---|---|---|
| c1_open_tasks_mcp | A | 1 | 20261127-212144-51bab7 | False | n/a | 3381 | 81 | - | 1 | - | [events](trials/c1_open_tasks_mcp/A1/runs/20261127-212144-51bab7/events.jsonl) |
| c1_open_tasks_mcp | B | 1 | 20261127-212149-411b05 | True | True | 9661 | 119 | open-tasks-export | 1 | - | [events](trials/c1_open_tasks_mcp/B1/runs/20261127-212149-411b05/events.jsonl) |
| c1_open_tasks_mcp | A | 2 | 20261127-212155-c86dd8 | False | n/a | 3381 | 81 | - | 1 | - | [events](trials/c1_open_tasks_mcp/A2/runs/20261127-212155-c86dd8/events.jsonl) |
| c1_open_tasks_mcp | B | 2 | 20261127-212158-bedc33 | True | True | 9661 | 129 | open-tasks-export | 1 | - | [events](trials/c1_open_tasks_mcp/B2/runs/20261127-212158-bedc33/events.jsonl) |
| c1_open_tasks_mcp | A | 3 | 20261127-212205-a23b70 | False | n/a | 3381 | 82 | - | 1 | - | [events](trials/c1_open_tasks_mcp/A3/runs/20261127-212205-a23b70/events.jsonl) |
| c1_open_tasks_mcp | B | 3 | 20261127-212209-5bea38 | True | True | 9661 | 129 | open-tasks-export | 1 | - | [events](trials/c1_open_tasks_mcp/B3/runs/20261127-212209-5bea38/events.jsonl) |
| c2_sprint_report | A | 1 | 20261127-212215-797ab9 | True | n/a | 3842 | 280 | - | 0 | - | [events](trials/c2_sprint_report/A1/runs/20261127-212215-797ab9/events.jsonl) |
| c2_sprint_report | B | 1 | 20261127-212221-fe0c8f | False | False | 4989 | 369 | - | 0 | - | [events](trials/c2_sprint_report/B1/runs/20261127-212221-fe0c8f/events.jsonl) |
| c2_sprint_report | A | 2 | 20261127-212228-aa935d | True | n/a | 3913 | 343 | - | 0 | - | [events](trials/c2_sprint_report/A2/runs/20261127-212228-aa935d/events.jsonl) |
| c2_sprint_report | B | 2 | 20261127-212234-cc0c84 | False | False | 4907 | 295 | - | 0 | - | [events](trials/c2_sprint_report/B2/runs/20261127-212234-cc0c84/events.jsonl) |
| c2_sprint_report | A | 3 | 20261127-212240-0cee28 | True | n/a | 3912 | 333 | - | 0 | - | [events](trials/c2_sprint_report/A3/runs/20261127-212240-0cee28/events.jsonl) |
| c2_sprint_report | B | 3 | 20261127-212247-90672b | False | False | 4986 | 363 | - | 0 | - | [events](trials/c2_sprint_report/B3/runs/20261127-212247-90672b/events.jsonl) |
| c3_fix_order_no_skill | A | 1 | 20261127-212254-037341 | True | n/a | 5999 | 316 | - | 0 | - | [events](trials/c3_fix_order_no_skill/A1/runs/20261127-212254-037341/events.jsonl) |
| c3_fix_order_no_skill | B | 1 | 20261127-212303-2ce7e4 | True | True | 7714 | 311 | - | 0 | - | [events](trials/c3_fix_order_no_skill/B1/runs/20261127-212303-2ce7e4/events.jsonl) |
| c3_fix_order_no_skill | A | 2 | 20261127-212312-1ce97b | True | n/a | 5811 | 226 | - | 0 | - | [events](trials/c3_fix_order_no_skill/A2/runs/20261127-212312-1ce97b/events.jsonl) |
| c3_fix_order_no_skill | B | 2 | 20261127-212319-7f5d8a | True | True | 7692 | 302 | - | 0 | - | [events](trials/c3_fix_order_no_skill/B2/runs/20261127-212319-7f5d8a/events.jsonl) |
| c3_fix_order_no_skill | A | 3 | 20261127-212327-dcfed0 | True | n/a | 5807 | 249 | - | 0 | - | [events](trials/c3_fix_order_no_skill/A3/runs/20261127-212327-dcfed0/events.jsonl) |
| c3_fix_order_no_skill | B | 3 | 20261127-212334-b3b33c | True | True | 7672 | 292 | - | 0 | - | [events](trials/c3_fix_order_no_skill/B3/runs/20261127-212334-b3b33c/events.jsonl) |
| c4_incomplete_report | A | 1 | 20261127-212342-73cdfa | False | n/a | 5195 | 501 | - | 0 | - | [events](trials/c4_incomplete_report/A1/runs/20261127-212342-73cdfa/events.jsonl) |
| c4_incomplete_report | B | 1 | 20261127-212352-b3c414 | False | False | 6801 | 725 | - | 0 | - | [events](trials/c4_incomplete_report/B1/runs/20261127-212352-b3c414/events.jsonl) |
| c4_incomplete_report | A | 2 | 20261127-212402-a0f815 | False | n/a | 5215 | 535 | - | 0 | - | [events](trials/c4_incomplete_report/A2/runs/20261127-212402-a0f815/events.jsonl) |
| c4_incomplete_report | B | 2 | 20261127-212411-eaa18a | True | True | 11963 | 388 | sprint-report | 0 | - | [events](trials/c4_incomplete_report/B2/runs/20261127-212411-eaa18a/events.jsonl) |
| c4_incomplete_report | A | 3 | 20261127-212423-655f39 | False | n/a | 5329 | 648 | - | 0 | - | [events](trials/c4_incomplete_report/A3/runs/20261127-212423-655f39/events.jsonl) |
| c4_incomplete_report | B | 3 | 20261127-212433-2e7b0e | True | True | 10151 | 332 | sprint-report | 0 | - | [events](trials/c4_incomplete_report/B3/runs/20261127-212433-2e7b0e/events.jsonl) |
| c5_mcp_failure | A | 1 | 20261127-212442-4ea866 | True | n/a | 1857 | 72 | - | 1 | - | [events](trials/c5_mcp_failure/A1/runs/20261127-212442-4ea866/events.jsonl) |
| c5_mcp_failure | B | 1 | 20261127-212444-c8dcab | True | True | 4402 | 65 | open-tasks-export | 1 | - | [events](trials/c5_mcp_failure/B1/runs/20261127-212444-c8dcab/events.jsonl) |
| c5_mcp_failure | A | 2 | 20261127-212448-76673c | True | n/a | 1857 | 57 | - | 1 | - | [events](trials/c5_mcp_failure/A2/runs/20261127-212448-76673c/events.jsonl) |
| c5_mcp_failure | B | 2 | 20261127-212451-2553c7 | True | True | 4402 | 67 | open-tasks-export | 1 | - | [events](trials/c5_mcp_failure/B2/runs/20261127-212451-2553c7/events.jsonl) |
| c5_mcp_failure | A | 3 | 20261127-212455-135757 | True | n/a | 1857 | 72 | - | 1 | - | [events](trials/c5_mcp_failure/A3/runs/20261127-212455-135757/events.jsonl) |
| c5_mcp_failure | B | 3 | 20261127-212458-860a8b | True | True | 4402 | 65 | open-tasks-export | 1 | - | [events](trials/c5_mcp_failure/B3/runs/20261127-212458-860a8b/events.jsonl) |

## Причины неуспеха

- c1_open_tasks_mcp A1: outcome: wrong set of ids; routing: version A has no skills
- c1_open_tasks_mcp A2: outcome: wrong set of ids; routing: version A has no skills
- c1_open_tasks_mcp A3: outcome: wrong set of ids; routing: version A has no skills
- c2_sprint_report B1: outcome: overdue tasks missing: ['T6']; tasks wrongly listed as overdue: ['T2']; routing: not loaded: ['sprint-report']
- c2_sprint_report B2: outcome: overdue tasks missing: ['T6']; routing: not loaded: ['sprint-report']
- c2_sprint_report B3: outcome: tasks wrongly listed as overdue: ['T2']; routing: not loaded: ['sprint-report']
- c4_incomplete_report A1: outcome: open: expected 4, got None; in_progress: expected 2, got None; closed: expected 2, got None; no overdue section; routing: version A has no skills
- c4_incomplete_report B1: outcome: overdue tasks missing: ['T6']; routing: not loaded: ['sprint-report']
- c4_incomplete_report A2: outcome: open: expected 4, got 5; no overdue section; routing: version A has no skills
- c4_incomplete_report A3: outcome: open: expected 4, got 5; no overdue section; routing: version A has no skills
