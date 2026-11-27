# Проверка обновления sprint-report: 1.0.0 -> 1.1.0

**Правило (задано до запуска).** Обновление принимается, только если одновременно: (1) ни в одном сценарии число успешных попыток новой версии не меньше, чем у старой; (2) ошибок выбора скиллов у новой версии не больше; (3) usage полный во всех trials обеих версий и средний расход input+output токенов на trial вырос не более чем на 25%. Иначе отклоняется.

**Решение: accepted.** все условия правила выполнены

| сценарий | old pass | new pass | old routing ошибок | new routing ошибок |
|---|---|---|---|---|
| c1_open_tasks_mcp | 3/3 | 3/3 | 0 | 0 |
| c2_sprint_report | 0/3 | 1/3 | 3 | 3 |
| c3_fix_order_no_skill | 3/3 | 3/3 | 0 | 0 |
| c4_incomplete_report | 1/3 | 2/3 | 1 | 1 |
| c5_mcp_failure | 3/3 | 3/3 | 0 | 0 |

Средний расход токенов на trial: old 7532.066666666667, new 7585.266666666666 (usage полный: old True, new True).

sha256 SKILL.md: old 7fad84f65a03743d943ebe3053c7331a90217e487400cc9095e6eab266b77bf5, new 0a3780872a7b116b13c9471f82b324e12ad3c9d6a142c7569ea8a4e630df5aaa.

Решение относится к учебному набору сценариев, а не ко всем возможным задачам.

Откат принятого обновления: `python3 -m agent market rollback sprint-report` или в chat: `/rollback-skill sprint-report`.

---

# Сводка eval: update-sprint-report-1.0.0-to-1.1.0

**Гипотеза.** Новая версия sprint-report 1.1.0 не хуже установленной 1.0.0 на учебном наборе.

**Критерии.** Обновление принимается, только если одновременно: (1) ни в одном сценарии число успешных попыток новой версии не меньше, чем у старой; (2) ошибок выбора скиллов у новой версии не больше; (3) usage полный во всех trials обеих версий и средний расход input+output токенов на trial вырос не более чем на 25%. Иначе отклоняется.

Режим: auto; подтверждения в eval: deny; бюджет на trial: {"model_calls": 20, "tool_calls": 40, "side_effects": 10, "result_bytes": 200000}; trials на версию: 3. Строк результатов: 30.
Модель: {"kind": "openai-compatible", "base_url": "https://openai.api.proxyapi.ru/v1", "model": "openai/gpt-4.1-mini", "temperature": 0.0, "no_think": false}

## Итог по сценариям

| сценарий | версия | outcome pass | routing ошибок | tools pass | ср. input | ср. output | usage | ср. MCP-вызовов | ср. вызовов | ошибки |
|---|---|---|---|---|---|---|---|---|---|---|
| c1_open_tasks_mcp | old | 3/3 | 0 | 3/3 | 9661 | 125 | complete | 1 | 4 | 0 |
| c1_open_tasks_mcp | new | 3/3 | 0 | 3/3 | 9661 | 124 | complete | 1 | 4 | 0 |
| c2_sprint_report | old | 0/3 | 3 | 3/3 | 5028 | 412 | complete | 0 | 3 | 0 |
| c2_sprint_report | new | 1/3 | 3 | 3/3 | 4957 | 342 | complete | 0 | 3 | 0 |
| c3_fix_order_no_skill | old | 3/3 | 0 | 3/3 | 7748 | 331 | complete | 0 | 5 | 0 |
| c3_fix_order_no_skill | new | 3/3 | 0 | 3/3 | 8175 | 303 | complete | 0 | 5 | 0 |
| c4_incomplete_report | old | 1/3 | 1 | 3/3 | 9532 | 356 | complete | 0 | 5 | 0 |
| c4_incomplete_report | new | 2/3 | 1 | 3/3 | 9325 | 569 | complete | 0 | 5 | 0 |
| c5_mcp_failure | old | 3/3 | 0 | 3/3 | 4402 | 65 | complete | 1 | 2 | 0 |
| c5_mcp_failure | new | 3/3 | 0 | 3/3 | 4402 | 68 | complete | 1 | 2 | 0 |

## Итог по версиям

- old: успешных 10/15, ср. input 7274, ср. output 258, ошибок выбора скиллов 4
- new: успешных 12/15, ср. input 7304, ср. output 281, ошибок выбора скиллов 4

## Все trials

| case_id | A/B | trial | run_id | outcome_pass | routing_pass_or_na | input_tokens | output_tokens | skill_activations | mcp_calls | error | журнал |
|---|---|---|---|---|---|---|---|---|---|---|---|
| c1_open_tasks_mcp | old | 1 | 20261127-212622-9090cf | True | True | 9661 | 119 | open-tasks-export | 1 | - | [events](trials/c1_open_tasks_mcp/old1/runs/20261127-212622-9090cf/events.jsonl) |
| c1_open_tasks_mcp | new | 1 | 20261127-212629-24fe29 | True | True | 9661 | 129 | open-tasks-export | 1 | - | [events](trials/c1_open_tasks_mcp/new1/runs/20261127-212629-24fe29/events.jsonl) |
| c1_open_tasks_mcp | old | 2 | 20261127-212634-de9a2e | True | True | 9661 | 129 | open-tasks-export | 1 | - | [events](trials/c1_open_tasks_mcp/old2/runs/20261127-212634-de9a2e/events.jsonl) |
| c1_open_tasks_mcp | new | 2 | 20261127-212640-0b5d84 | True | True | 9661 | 114 | open-tasks-export | 1 | - | [events](trials/c1_open_tasks_mcp/new2/runs/20261127-212640-0b5d84/events.jsonl) |
| c1_open_tasks_mcp | old | 3 | 20261127-212647-5241c4 | True | True | 9661 | 128 | open-tasks-export | 1 | - | [events](trials/c1_open_tasks_mcp/old3/runs/20261127-212647-5241c4/events.jsonl) |
| c1_open_tasks_mcp | new | 3 | 20261127-212653-22b485 | True | True | 9661 | 128 | open-tasks-export | 1 | - | [events](trials/c1_open_tasks_mcp/new3/runs/20261127-212653-22b485/events.jsonl) |
| c2_sprint_report | old | 1 | 20261127-212659-d13a5a | False | False | 5169 | 562 | - | 0 | - | [events](trials/c2_sprint_report/old1/runs/20261127-212659-d13a5a/events.jsonl) |
| c2_sprint_report | new | 1 | 20261127-212708-c63227 | True | False | 5020 | 408 | - | 0 | - | [events](trials/c2_sprint_report/new1/runs/20261127-212708-c63227/events.jsonl) |
| c2_sprint_report | old | 2 | 20261127-212716-fd4b20 | False | False | 4968 | 356 | - | 0 | - | [events](trials/c2_sprint_report/old2/runs/20261127-212716-fd4b20/events.jsonl) |
| c2_sprint_report | new | 2 | 20261127-212723-ddb800 | False | False | 4897 | 283 | - | 0 | - | [events](trials/c2_sprint_report/new2/runs/20261127-212723-ddb800/events.jsonl) |
| c2_sprint_report | old | 3 | 20261127-212729-0f80e6 | False | False | 4948 | 317 | - | 0 | - | [events](trials/c2_sprint_report/old3/runs/20261127-212729-0f80e6/events.jsonl) |
| c2_sprint_report | new | 3 | 20261127-212735-026107 | False | False | 4955 | 335 | - | 0 | - | [events](trials/c2_sprint_report/new3/runs/20261127-212735-026107/events.jsonl) |
| c3_fix_order_no_skill | old | 1 | 20261127-212742-9c3387 | True | True | 7884 | 405 | - | 0 | - | [events](trials/c3_fix_order_no_skill/old1/runs/20261127-212742-9c3387/events.jsonl) |
| c3_fix_order_no_skill | new | 1 | 20261127-212752-bf4ecd | True | True | 7684 | 288 | - | 0 | - | [events](trials/c3_fix_order_no_skill/new1/runs/20261127-212752-bf4ecd/events.jsonl) |
| c3_fix_order_no_skill | old | 2 | 20261127-212801-4e4498 | True | True | 7690 | 306 | - | 0 | - | [events](trials/c3_fix_order_no_skill/old2/runs/20261127-212801-4e4498/events.jsonl) |
| c3_fix_order_no_skill | new | 2 | 20261127-212810-7a5b85 | True | True | 7792 | 333 | - | 0 | - | [events](trials/c3_fix_order_no_skill/new2/runs/20261127-212810-7a5b85/events.jsonl) |
| c3_fix_order_no_skill | old | 3 | 20261127-212820-0c9b3a | True | True | 7670 | 282 | - | 0 | - | [events](trials/c3_fix_order_no_skill/old3/runs/20261127-212820-0c9b3a/events.jsonl) |
| c3_fix_order_no_skill | new | 3 | 20261127-212828-ac7f5f | True | True | 9050 | 287 | - | 0 | - | [events](trials/c3_fix_order_no_skill/new3/runs/20261127-212828-ac7f5f/events.jsonl) |
| c4_incomplete_report | old | 1 | 20261127-212837-39dd09 | False | False | 6478 | 398 | - | 0 | - | [events](trials/c4_incomplete_report/old1/runs/20261127-212837-39dd09/events.jsonl) |
| c4_incomplete_report | new | 1 | 20261127-212846-9d9854 | False | False | 6811 | 723 | - | 0 | - | [events](trials/c4_incomplete_report/new1/runs/20261127-212846-9d9854/events.jsonl) |
| c4_incomplete_report | old | 2 | 20261127-212900-2439d1 | True | True | 10154 | 334 | sprint-report | 0 | - | [events](trials/c4_incomplete_report/old2/runs/20261127-212900-2439d1/events.jsonl) |
| c4_incomplete_report | new | 2 | 20261127-212911-af17ab | True | True | 10578 | 490 | sprint-report | 0 | - | [events](trials/c4_incomplete_report/new2/runs/20261127-212911-af17ab/events.jsonl) |
| c4_incomplete_report | old | 3 | 20261127-212923-702829 | False | True | 11964 | 335 | sprint-report | 0 | - | [events](trials/c4_incomplete_report/old3/runs/20261127-212923-702829/events.jsonl) |
| c4_incomplete_report | new | 3 | 20261127-212935-2cb078 | True | True | 10585 | 495 | sprint-report | 0 | - | [events](trials/c4_incomplete_report/new3/runs/20261127-212935-2cb078/events.jsonl) |
| c5_mcp_failure | old | 1 | 20261127-212945-da26d6 | True | True | 4402 | 66 | open-tasks-export | 1 | - | [events](trials/c5_mcp_failure/old1/runs/20261127-212945-da26d6/events.jsonl) |
| c5_mcp_failure | new | 1 | 20261127-212952-8e00c6 | True | True | 4402 | 65 | open-tasks-export | 1 | - | [events](trials/c5_mcp_failure/new1/runs/20261127-212952-8e00c6/events.jsonl) |
| c5_mcp_failure | old | 2 | 20261127-212956-cac2d4 | True | True | 4402 | 65 | open-tasks-export | 1 | - | [events](trials/c5_mcp_failure/old2/runs/20261127-212956-cac2d4/events.jsonl) |
| c5_mcp_failure | new | 2 | 20261127-213000-4154a0 | True | True | 4402 | 65 | open-tasks-export | 1 | - | [events](trials/c5_mcp_failure/new2/runs/20261127-213000-4154a0/events.jsonl) |
| c5_mcp_failure | old | 3 | 20261127-213003-a612b7 | True | True | 4402 | 65 | open-tasks-export | 1 | - | [events](trials/c5_mcp_failure/old3/runs/20261127-213003-a612b7/events.jsonl) |
| c5_mcp_failure | new | 3 | 20261127-213007-ed4fa6 | True | True | 4402 | 75 | open-tasks-export | 1 | - | [events](trials/c5_mcp_failure/new3/runs/20261127-213007-ed4fa6/events.jsonl) |

## Причины неуспеха

- c2_sprint_report old1: outcome: tasks wrongly listed as overdue: ['T2', 'T3', 'T4', 'T5', 'T7']; routing: not loaded: ['sprint-report']
- c2_sprint_report new1: outcome: counts, overdue tasks and incomplete data are correct; routing: not loaded: ['sprint-report']
- c2_sprint_report old2: outcome: overdue tasks missing: ['T6']; tasks wrongly listed as overdue: ['T2']; routing: not loaded: ['sprint-report']
- c2_sprint_report new2: outcome: overdue tasks missing: ['T6']; routing: not loaded: ['sprint-report']
- c2_sprint_report old3: outcome: overdue tasks missing: ['T6']; tasks wrongly listed as overdue: ['T2']; routing: not loaded: ['sprint-report']
- c2_sprint_report new3: outcome: overdue tasks missing: ['T6']; routing: not loaded: ['sprint-report']
- c4_incomplete_report old1: outcome: overdue tasks missing: ['T6']; routing: not loaded: ['sprint-report']
- c4_incomplete_report new1: outcome: overdue tasks missing: ['T6']; routing: not loaded: ['sprint-report']
- c4_incomplete_report old3: outcome: overdue tasks missing: ['T6']; routing: ok

