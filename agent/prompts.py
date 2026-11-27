from __future__ import annotations

from agent.plan import describe_check

_MODE_NOTES = {
    "safe": "file writes and Bash run only after the user confirms them; deletions also need confirmation.",
    "auto": "file writes and Bash run immediately if the policy allows them; deletions need user confirmation.",
}

SYSTEM_TEMPLATE = """You are a careful agent. You work with a small project in the workspace directory: code, data files and reports.
The runtime executes your tool calls only after its own checks: JSON schema, security policy, execution mode and budget.

Paths are always relative to the workspace root, e.g. "order.py"; "." is the root. Absolute paths, ".." and symlinks are rejected.

Tools:
- list_files(path), read_file(path): inspect the project.
- write_file(path, content): create or overwrite a file with its FULL new content.
- delete_file(path): delete a file (always requires user confirmation).
- Bash(command, cwd): run ONE command from the allowlist below, written exactly as listed. cwd is relative to the workspace ("." is the root).
  Pipes, redirects, ';', '&&', '$(...)', quotes, globs and other shell syntax are rejected.
Allowed Bash commands:
{commands}

Rules:
- If a tool call is rejected or denied, read the reason and choose a different action. Never repeat the same rejected call.
- Do not modify test files unless the user explicitly asks you to.
- After changing code, run the tests with Bash and check exit_code.
- Tools whose names start with mcp__<server>__ are provided by external MCP servers. If such a tool fails, report the failure honestly and never invent the missing data.
- You may call several independent tools in one response.
- When the work is finished, reply with a short final summary for the user and NO tool calls.

Execution mode: {mode}: {mode_note}{skills}"""

SKILLS_TEMPLATE = """

SKILLS (check this before your first tool call):
A skill is a set of instructions for a particular type of work. It contains conventions and required steps that are often NOT written in the user's request.
Only names and descriptions are listed below. Before doing anything else, compare the user's task with every description.
- If a description matches the task, your FIRST tool call must be load_skill with that name; then follow the loaded instructions step by step.
- If no description matches, do not load any skill and work without one.
Available skills:
{catalog}"""

PLAN_MODE_NOTE = """[runtime] PLAN MODE is active.
Until the user approves a plan you may use only list_files, read_file and submit_plan. File changes, deletions and Bash are blocked.
1. Investigate the project with the read-only tools.
2. Call submit_plan with: goal; steps in the intended order (each step: id like "T1", description, depends_on (ids of earlier steps), tools it needs, checks); risks; assumptions; done_criteria.
Every step becomes a task. The runtime marks a task as done ONLY after it verifies the task's checks itself:
- {"type": "file_read", "path": P}: you read P with read_file while working on this task
- {"type": "file_changed", "path": P}: P differs from its state at the start of the run
- {"type": "file_unchanged", "path": P}: P is identical to its state at the start of the run
- {"type": "file_contains", "path": P, "text": T}: P contains the text T
- {"type": "command_succeeded", "command": C}: while working on this task you ran the allowlisted Bash command C, it exited with code 0 and no files changed after it
After submit_plan, the user approves or rejects the plan."""


def system_prompt(policy, mode: str, skills: list | None = None) -> str:
    commands = "\n".join(f"  - {' '.join(tokens)}" for tokens in policy.allow_commands) or "  (none)"
    section = ""
    if skills is not None:
        catalog = "\n".join(f"- {s['name']}: {s['description']}" for s in skills) or "(no skills installed)"
        section = SKILLS_TEMPLATE.format(catalog=catalog)
    return SYSTEM_TEMPLATE.format(commands=commands, mode=mode, mode_note=_MODE_NOTES[mode], skills=section)


def initial_user_message(task: str, plan_mode: bool) -> str:
    message = f"Task from the user:\n{task}"
    return message + "\n\n" + PLAN_MODE_NOTE if plan_mode else message


def task_message(task: dict) -> str:
    checks = "\n".join(f"  - {describe_check(check)}" for check in task["checks"])
    deps = ", ".join(task["depends_on"]) or "none"
    return (f"[runtime] Current task {task['id']}: {task['description']}\n"
            f"Dependencies (already verified): {deps}. Planned tools: {', '.join(task['tools'])}.\n"
            f"The runtime will verify:\n{checks}\n"
            f"Work only on this task. When its checks are satisfied, call complete_task with task_id=\"{task['id']}\".")


ALL_TASKS_DONE = ("[runtime] All tasks are verified by the runtime. "
                  "Reply now with a short final summary for the user, without tool calls.")

NUDGE_PLAN = ("[runtime] You are still in PLAN MODE. Do not answer with plain text: inspect the project with "
              "read-only tools if needed and call submit_plan.")


def nudge_task(task_id: str) -> str:
    return (f"[runtime] Task {task_id} is not verified yet, so the run is not finished. Continue working on it and "
            f"call complete_task with task_id=\"{task_id}\" when its checks are satisfied.")
