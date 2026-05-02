"""
prompt_planner.py — ORION Prompt Engineering Pipeline.

Converts simple natural language task descriptions into highly structured,
tool-call-specific, dependency-ordered prompts that the agent reliably executes.

Usage:
    python prompt_planner.py "get TSLA data and email a summary to john@example.com"
    python prompt_planner.py --interactive
    python prompt_planner.py --file tasks.txt

Architecture:
    User input (natural language)
        ↓
    [Stage 1: Intent Extraction]    — classify domain, tools needed, data deps
        ↓
    [Stage 2: Dependency Graph]     — order steps so outputs feed inputs
        ↓
    [Stage 3: Tool-Specific Prompt] — generate exact tool call instructions
        ↓
    [Stage 4: Structured Output]    — emit final agent prompt + metadata
        ↓
    Feed to agent graph via CLI / API / WebSocket
"""
import asyncio
import json
import sys
import argparse
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path
from datetime import datetime

# ── Data Models ────────────────────────────────────────────────────────────────

@dataclass
class PlannedStep:
    """A single planned step with tool specification."""
    index: int
    action_type: str          # USE_API | RUN_POWERSHELL | SNAPSHOT | CLICK_AT | etc.
    tool_name: str            # Exact tool name to call
    tool_args: dict           # Exact args to pass
    description: str          # Human-readable description
    depends_on: list[int]     # Step indices this step depends on
    data_input_from: list[int] # Steps whose OUTPUT this step consumes
    can_fail_gracefully: bool # If True, failure skips rather than aborts
    expected_output_type: str # "json_data" | "file_path" | "email_id" | "screenshot" | "text"
    verification: str         # What to check to know this step succeeded


@dataclass
class StructuredPlan:
    """Complete structured execution plan."""
    original_prompt: str
    structured_prompt: str      # The full prompt to feed to the agent
    steps: list[PlannedStep]
    estimated_duration_s: int
    required_tools: list[str]
    required_servers: list[str] # windows | playwright | fincept | google
    data_flow: dict             # step_idx -> what data it produces
    error_strategy: str         # abort | skip_and_continue | best_effort
    metadata: dict = field(default_factory=dict)


# ── Tool Registry ──────────────────────────────────────────────────────────────

TOOL_REGISTRY = {
    # Fincept MCP
    "market_data": {
        "server": "fincept", "category": "financial",
        "args": ["symbol", "period", "interval", "include_fundamentals"],
        "output_type": "json_data",
        "keywords": ["stock", "price", "market", "ticker", "tsla", "aapl", "btc",
                     "crypto", "financial data", "trading data", "chart", "ohlcv"],
    },
    "sec_filings": {
        "server": "fincept", "category": "financial",
        "args": ["ticker", "form_type", "limit"],
        "output_type": "json_data",
        "keywords": ["sec", "filing", "10-k", "10-q", "edgar", "annual report"],
    },
    # Google
    "gmail_send_email": {
        "server": "google", "category": "email",
        "args": ["to", "subject", "body", "cc"],
        "output_type": "email_id",
        "keywords": ["email", "send", "mail to", "notify", "message to"],
    },
    "gmail_read_inbox": {
        "server": "google", "category": "email",
        "args": ["max_results", "query"],
        "output_type": "json_data",
        "keywords": ["read email", "check inbox", "unread", "email from"],
    },
    "calendar_list_events": {
        "server": "google", "category": "calendar",
        "args": ["days_ahead"],
        "output_type": "json_data",
        "keywords": ["calendar", "events", "schedule", "meetings", "today's meetings"],
    },
    "calendar_create_event": {
        "server": "google", "category": "calendar",
        "args": ["title", "start_datetime", "end_datetime", "description", "attendees"],
        "output_type": "text",
        "keywords": ["create event", "schedule meeting", "add to calendar"],
    },
    "drive_search_files": {
        "server": "google", "category": "drive",
        "args": ["query", "max_results"],
        "output_type": "json_data",
        "keywords": ["drive", "find file", "search document", "google drive", "find report"],
    },
    "sheets_read_range": {
        "server": "google", "category": "sheets",
        "args": ["spreadsheet_id", "range_notation"],
        "output_type": "json_data",
        "keywords": ["spreadsheet", "sheet", "excel online", "read cells"],
    },
    # Windows MCP
    "FileSystem": {
        "server": "windows", "category": "filesystem",
        "args": ["mode", "path", "content", "overwrite"],
        "output_type": "file_path",
        "keywords": ["save file", "write file", "create file", "write to disk"],
    },
    "PowerShell": {
        "server": "windows", "category": "system",
        "args": ["command", "timeout"],
        "output_type": "text",
        "keywords": ["powershell", "run command", "system command", "ps"],
    },
    "Snapshot": {
        "server": "windows", "category": "vision",
        "args": [],
        "output_type": "screenshot",
        "keywords": ["screenshot", "take snapshot", "capture screen"],
    },
    "App": {
        "server": "windows", "category": "gui",
        "args": ["action", "name"],
        "output_type": "text",
        "keywords": ["open app", "launch", "start application", "close app"],
    },
    # Playwright
    "playwright_navigate": {
        "server": "playwright", "category": "browser",
        "args": ["url"],
        "output_type": "text",
        "keywords": ["browse", "open website", "navigate to", "go to url"],
    },
    "playwright_click": {
        "server": "playwright", "category": "browser",
        "args": ["selector"],
        "output_type": "text",
        "keywords": ["click link", "click button", "dom click"],
    },
    # File tools
    "file_read_text": {
        "server": "local", "category": "filesystem",
        "args": ["path", "max_lines"],
        "output_type": "text",
        "keywords": ["read file", "open file", "load file"],
    },
    "file_write_text": {
        "server": "local", "category": "filesystem",
        "args": ["path", "content", "overwrite"],
        "output_type": "file_path",
        "keywords": ["write", "save", "create file"],
    },
}


# ── Stage 1: Intent Extraction ─────────────────────────────────────────────────

STAGE1_SYSTEM = """You are a task decomposition engine for an AI agent system.
Given a natural language task, extract:
1. All distinct sub-goals in dependency order
2. What real-world data each sub-goal needs
3. Which tools from the registry are needed
4. Data flow between steps (what output of step N feeds into step M)

TOOL REGISTRY (available tools):
{tool_registry}

Respond ONLY with a JSON object matching this schema exactly:
{{
  "task_summary": "one-line description",
  "sub_goals": [
    {{
      "id": 1,
      "goal": "what this step achieves",
      "tool": "exact tool name from registry",
      "args_template": {{"arg_name": "value or <<step_N_output>>"}},
      "depends_on": [],
      "can_fail_gracefully": false,
      "expected_output": "json_data|file_path|email_id|text|screenshot"
    }}
  ],
  "data_sources_needed": ["list of external data sources"],
  "required_servers": ["windows", "fincept", "google", "playwright"],
  "error_strategy": "abort|skip_and_continue|best_effort",
  "estimated_steps": 6
}}
"""

STAGE2_SYSTEM = """You are a prompt compiler for an AI Windows automation agent.
Given a structured execution plan, generate the FINAL AGENT PROMPT.

The agent prompt must:
1. State the overall goal clearly
2. List each step with EXACT tool name and EXACT args
3. Show data dependencies explicitly: "USE the <<output of step N>> as input to step M"
4. Include verification instructions for each step
5. Include error handling: what to do if a step fails
6. Be written as IMPERATIVE instructions, not requests

IMPORTANT RULES for the output prompt:
- Every step that fetches data must store it in a named variable: <<step_N_result>>
- Every step that uses data must reference the exact variable
- Tool args containing data from previous steps must use: <<step_N_result.field_name>>
- Include a VERIFY step after every data-altering action
- Do NOT include vague instructions like "use the data" — specify exactly how

Output ONLY the final agent prompt text. No JSON wrapper, no preamble."""


async def _call_nim_llm(system: str, user: str, max_tokens: int = 2048) -> str:
    """Call NVIDIA NIM LLM directly via httpx (no langchain dependency)."""
    try:
        import config as cfg
        import httpx

        response = httpx.post(
            f"{cfg.NIM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg.NVIDIA_NIM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg.NIM_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError(f"NIM LLM call failed: {exc}") from exc


# ── Stage 1 ────────────────────────────────────────────────────────────────────

async def _extract_intent(user_prompt: str) -> dict:
    """Stage 1: Extract structured intent from natural language."""
    # Build compact tool registry for the prompt
    registry_lines = []
    for name, info in TOOL_REGISTRY.items():
        args_str = ", ".join(info["args"][:4])
        registry_lines.append(
            f"  {name} [{info['server']}] ({args_str}) — triggers: {', '.join(info['keywords'][:3])}"
        )
    registry_str = "\n".join(registry_lines)

    system = STAGE1_SYSTEM.format(tool_registry=registry_str)
    result = await _call_nim_llm(system, user_prompt, max_tokens=1500)

    # Strip markdown code blocks if present
    content = result.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    return json.loads(content)


# ── Stage 2 ────────────────────────────────────────────────────────────────────

async def _build_structured_prompt(intent: dict, original_prompt: str) -> str:
    """Stage 2: Generate the final structured agent prompt."""
    intent_str = json.dumps(intent, indent=2)
    user_input = (
        f"Original user request: {original_prompt}\n\n"
        f"Extracted plan:\n{intent_str}\n\n"
        f"Generate the final agent execution prompt."
    )
    return await _call_nim_llm(STAGE2_SYSTEM, user_input, max_tokens=2000)


# ── Stage 3: Local Assembly ────────────────────────────────────────────────────

def _assemble_plan(intent: dict, structured_prompt: str, original_prompt: str) -> StructuredPlan:
    """Stage 3: Assemble a StructuredPlan from the LLM outputs."""
    steps = []
    for sg in intent.get("sub_goals", []):
        tool_name = sg.get("tool", "unknown")
        tool_info = TOOL_REGISTRY.get(tool_name, {})
        step = PlannedStep(
            index=sg.get("id", 0),
            action_type=_infer_action_type(tool_name),
            tool_name=tool_name,
            tool_args=sg.get("args_template", {}),
            description=sg.get("goal", ""),
            depends_on=sg.get("depends_on", []),
            data_input_from=[d for d in sg.get("depends_on", [])],
            can_fail_gracefully=sg.get("can_fail_gracefully", False),
            expected_output_type=sg.get("expected_output", "text"),
            verification=f"Verify {tool_name} returned {sg.get('expected_output','data')}",
        )
        steps.append(step)

    required_tools = list({sg.get("tool", "") for sg in intent.get("sub_goals", [])})
    required_servers = intent.get("required_servers", [])

    # Estimate duration: ~15s per LLM call + tool overhead
    est = len(steps) * 18 + len([s for s in steps if s.tool_name == "Snapshot"]) * 3
    return StructuredPlan(
        original_prompt=original_prompt,
        structured_prompt=structured_prompt,
        steps=steps,
        estimated_duration_s=est,
        required_tools=required_tools,
        required_servers=required_servers,
        data_flow={sg["id"]: sg.get("expected_output", "text") for sg in intent.get("sub_goals", [])},
        error_strategy=intent.get("error_strategy", "skip_and_continue"),
        metadata={
            "generated_at": datetime.now().isoformat(),
            "task_summary": intent.get("task_summary", ""),
            "estimated_steps": intent.get("estimated_steps", len(steps)),
        },
    )


def _infer_action_type(tool_name: str) -> str:
    mapping = {
        "market_data": "USE_API", "sec_filings": "USE_API",
        "gmail_send_email": "USE_API", "gmail_read_inbox": "USE_API",
        "calendar_list_events": "USE_API", "calendar_create_event": "USE_API",
        "drive_search_files": "USE_API", "sheets_read_range": "USE_API",
        "PowerShell": "RUN_POWERSHELL", "FileSystem": "USE_FILESYSTEM",
        "Snapshot": "SNAPSHOT", "App": "LAUNCH",
        "playwright_navigate": "BROWSE", "playwright_click": "CLICK_AT",
        "file_read_text": "READ_FILE", "file_write_text": "WRITE_FILE",
    }
    return mapping.get(tool_name, "USE_API")


# ── Output Formatting ──────────────────────────────────────────────────────────

def _format_plan_summary(plan: StructuredPlan) -> str:
    """Format a human-readable plan summary for display."""
    lines = [
        "=" * 60,
        f"ORION PROMPT PLANNER — Structured Execution Plan",
        "=" * 60,
        f"Original: {plan.original_prompt}",
        f"Summary:  {plan.metadata.get('task_summary', 'N/A')}",
        f"Steps:    {len(plan.steps)} | Est. duration: ~{plan.estimated_duration_s}s",
        f"Servers:  {', '.join(plan.required_servers)}",
        f"Strategy: {plan.error_strategy}",
        "",
        "── Execution Steps ──",
    ]
    for step in plan.steps:
        deps = f" (after step {step.depends_on})" if step.depends_on else ""
        lines.append(f"  {step.index}. [{step.action_type}] {step.tool_name}{deps}")
        lines.append(f"     Goal: {step.description}")
        if step.tool_args:
            lines.append(f"     Args: {step.tool_args}")
        lines.append(f"     Output: {step.expected_output_type}")
        if step.can_fail_gracefully:
            lines.append(f"     ⚠ Can fail gracefully")

    lines.extend([
        "",
        "── Generated Agent Prompt ──",
        plan.structured_prompt,
        "=" * 60,
    ])
    return "\n".join(lines)


# ── Main Pipeline ──────────────────────────────────────────────────────────────

async def plan_prompt(user_prompt: str, verbose: bool = True) -> StructuredPlan:
    """
    Main entry point: convert natural language to a StructuredPlan.

    Args:
        user_prompt: Natural language task description
        verbose: Print progress to stdout

    Returns:
        StructuredPlan with structured_prompt ready to feed to the agent
    """
    if verbose:
        print(f"\n[1/3] Extracting intent from: '{user_prompt[:80]}'...")

    intent = await _extract_intent(user_prompt)

    if verbose:
        print(f"[2/3] Building structured prompt ({intent.get('estimated_steps', '?')} steps)...")

    structured_prompt = await _build_structured_prompt(intent, user_prompt)

    if verbose:
        print(f"[3/3] Assembling plan...")

    plan = _assemble_plan(intent, structured_prompt, user_prompt)

    if verbose:
        print(_format_plan_summary(plan))

    return plan


async def feed_to_agent(plan: StructuredPlan) -> str:
    """
    Feed the structured plan directly to the ORION agent graph.

    Returns the agent's final response.
    """
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))

        from agents.graph import build_graph
        from tools.mcp_client import multi_mcp_client
        from tools.google_tools import GOOGLE_TOOLS
        from tools.fintech_tools import FINTECH_TOOLS
        from tools.file_tools import FILE_TOOLS
        from tools.fincept_tools import launch_fincept_terminal
        from langchain_core.messages import HumanMessage

        # Use existing graph if available, else build minimal one
        mcp_tools = await multi_mcp_client.initialize_all()
        all_tools = mcp_tools + GOOGLE_TOOLS + FINTECH_TOOLS + FILE_TOOLS + [launch_fincept_terminal]
        graph = build_graph(tools=all_tools, mcp_client=multi_mcp_client)

        initial_state = {
            "messages": [HumanMessage(content=plan.structured_prompt)],
            "user_id": "prompt_planner",
        }
        thread_config = {"configurable": {"thread_id": f"planner_{datetime.now().strftime('%H%M%S')}"}}

        final_state = await graph.ainvoke(initial_state, config=thread_config)
        messages = final_state.get("messages", [])
        return messages[-1].content if messages else "No response"

    except Exception as exc:
        return f"Agent execution failed: {exc}"


# ── Interactive Mode ───────────────────────────────────────────────────────────

async def interactive_mode():
    """REPL for interactive prompt planning."""
    print("\nORION Prompt Planner — Interactive Mode")
    print("Type 'quit' to exit, 'run' to execute the last plan, 'save' to save plan.\n")

    last_plan: Optional[StructuredPlan] = None

    while True:
        try:
            user_input = input("Plan> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break
        if user_input.lower() == "run" and last_plan:
            print("\nFeeding plan to ORION agent...")
            result = await feed_to_agent(last_plan)
            print(f"\nAgent response:\n{result}\n")
            continue
        if user_input.lower().startswith("save") and last_plan:
            fname = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(fname, "w") as f:
                json.dump(asdict(last_plan), f, indent=2, default=str)
            print(f"Plan saved to {fname}")
            continue
        if user_input.lower() == "help":
            print("Commands: quit | run (execute last plan) | save | <natural language task>")
            continue

        try:
            last_plan = await plan_prompt(user_input)
        except Exception as exc:
            print(f"Planning failed: {exc}")


# ── File Batch Mode ────────────────────────────────────────────────────────────

async def batch_mode(file_path: str):
    """Process a file of tasks, one per line."""
    path = Path(file_path)
    if not path.exists():
        print(f"File not found: {file_path}")
        return

    tasks = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    print(f"Processing {len(tasks)} tasks from {file_path}...")

    plans = []
    for i, task in enumerate(tasks, 1):
        print(f"\n[{i}/{len(tasks)}] {task}")
        try:
            plan = await plan_prompt(task, verbose=False)
            plans.append(asdict(plan))
            print(f"  → {len(plan.steps)} steps, ~{plan.estimated_duration_s}s")
        except Exception as exc:
            print(f"  → FAILED: {exc}")

    out_file = path.stem + "_plans.json"
    with open(out_file, "w") as f:
        json.dump(plans, f, indent=2, default=str)
    print(f"\nAll plans saved to {out_file}")


# ── CLI Entry Point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ORION Prompt Planner — Convert natural language to structured agent prompts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python prompt_planner.py "get TSLA data and email a summary to john@example.com"
  python prompt_planner.py --interactive
  python prompt_planner.py --file tasks.txt
  python prompt_planner.py "open Chrome and search GitHub trending" --run
        """,
    )
    parser.add_argument("task", nargs="?", help="Natural language task to plan")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive REPL mode")
    parser.add_argument("--file", "-f", type=str, help="Batch process tasks from a file")
    parser.add_argument("--run", "-r", action="store_true", help="After planning, run the plan through ORION")
    parser.add_argument("--save", "-s", type=str, help="Save the generated plan to a JSON file")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress plan summary output")
    args = parser.parse_args()

    async def _main():
        if args.interactive:
            await interactive_mode()
        elif args.file:
            await batch_mode(args.file)
        elif args.task:
            plan = await plan_prompt(args.task, verbose=not args.quiet)
            if args.save:
                with open(args.save, "w") as f:
                    json.dump(asdict(plan), f, indent=2, default=str)
                print(f"\nPlan saved to {args.save}")
            if args.run:
                print("\nFeeding structured plan to ORION agent...")
                result = await feed_to_agent(plan)
                print(f"\nAgent response:\n{result}")
        else:
            parser.print_help()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
