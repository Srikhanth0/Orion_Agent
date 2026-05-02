# Agent Orion — Master Implementation Plan

> **Status**: Draft v1.0  
> **Scope**: Full architectural overhaul — planner, executor, vision loop, UI shell  
> **Priority order**: P0 = critical blocker → P3 = enhancement

---

## Table of Contents

1. [Problem Diagnosis](#1-problem-diagnosis)
2. [Architecture Changes Overview](#2-architecture-changes-overview)
3. [Phase 1 — Anti-Hallucination & Tool Integrity](#phase-1--anti-hallucination--tool-integrity)
4. [Phase 2 — Coordinate-Accurate Vision Loop](#phase-2--coordinate-accurate-vision-loop)
5. [Phase 3 — Planner Overhaul (Physical Decomposition)](#phase-3--planner-overhaul-physical-decomposition)
6. [Phase 4 — Windows MCP Full Utilization](#phase-4--windows-mcp-full-utilization)
7. [Phase 5 — Orion UI Shell (Floating Chat + Log Viewer)](#phase-5--orion-ui-shell-floating-chat--log-viewer)
8. [Phase 6 — Memory & Context Improvements](#phase-6--memory--context-improvements)
9. [File-by-File Change Map](#file-by-file-change-map)
10. [Testing Checklist](#testing-checklist)
11. [Dependency Additions](#dependency-additions)

---

## 1. Problem Diagnosis

### 1.1 Complex Task Breakdown Failure
**Root cause**: `agents/planner.py` generates *semantic* subtasks ("set up the interface") not *physical* steps ("take screenshot → read coordinates of button → click at (x,y)"). The 70B model plans like a human thinks, not like a robot acts.

**Symptom**: Executor receives vague subtasks → calls wrong tools → validator sees no visual change → loop fails.

### 1.2 Windows MCP Underutilization
**Root cause**: `agents/prompts.py` lists Windows MCP capabilities but does not *enforce* which tool to use for which action class. The LLM picks PowerShell for everything because it's familiar from training data.

**Symptom**: GUI tasks try PowerShell workarounds instead of native Click/Type/Snapshot tools.

### 1.3 Hallucination in Tool Results
**Root cause**: `executor_node` has `tool_choice="any"` but no mandatory post-tool verification. If a tool errors silently (returns a string that starts with "Error:"), the executor marks it success and moves on. The validator only catches this sometimes.

**Symptom**: Agent says "I opened Notepad" when it never did.

### 1.4 Coordinate Inaccuracy
**Root cause**: Two independent coordinate systems are used:
- Windows MCP `Click` uses screen-pixel coordinates.
- pyautogui (in `capture_screen.py`) uses its own DPI-scaled system.
Neither is calibrated against the current screen resolution or DPI scaling factor.

**Symptom**: Clicks land 50–150px away from the intended target on high-DPI displays.

### 1.5 No Desktop UI
**Root cause**: Only CLI / Telegram / Slack interfaces exist. There is no always-on local surface.

---

## 2. Architecture Changes Overview

```
CURRENT FLOW:
  Supervisor → Planner → Executor → Validator → Memory → Responder

IMPROVED FLOW:
  Supervisor
    ↓
    ├─ [chat] → Responder
    ├─ [simple] → ScreenCalibrator → Executor → Verifier → Responder
    └─ [complex] → PhysicalPlanner → ScreenCalibrator → Executor → Verifier
                                                             ↕ (retry loop)
                                                         Responder
                                                             ↓
                                                        UIBridge (WebSocket)
```

**New nodes added:**
| Node | Purpose |
|---|---|
| `ScreenCalibrator` | One-shot at task start: gets resolution + DPI, stores in state |
| `PhysicalPlanner` | Replaces Planner with forced physical decomposition |
| `Verifier` | Replaces Validator with stricter tool-result checking + vision diff |
| `UIBridge` | Streams state updates to the Orion UI via WebSocket |

---

## Phase 1 — Anti-Hallucination & Tool Integrity

**Priority**: P0 — Fix first, blocks everything else.

### 1.1 Strict Tool Result Parsing (`agents/executor.py`)

**Change**: After every tool call, parse the result string for known failure patterns before marking success.

```python
# Add to executor.py

FAILURE_PATTERNS = [
    "error:", "failed:", "exception:", "not found", "access denied",
    "permission denied", "cannot find", "does not exist", "traceback",
    "undefined", "null", "none returned"
]

def _is_tool_result_failure(result_str: str) -> bool:
    """Detect silent failures returned as strings instead of exceptions."""
    lowered = result_str.lower().strip()
    return any(p in lowered for p in FAILURE_PATTERNS) or len(result_str.strip()) == 0

# In the tool execution loop, replace:
#   "success": True
# with:
#   "success": not _is_tool_result_failure(result_str)
```

**Impact**: Catches ~80% of silent failures. Validator gets accurate `success` field.

### 1.2 Mandatory Result Evidence (`agents/executor.py`)

**Change**: After every tool call (not just GUI ones), append a `result_evidence` field containing the first 500 chars of the raw result. Validator uses this as ground truth.

```python
step_results.append({
    "step": current_idx + 1,
    "tool": tool_name,
    "args": tool_args,
    "result": result_str,
    "result_evidence": result_str[:500],   # ← NEW
    "success": not _is_tool_result_failure(result_str),
    "summary": f"{tool_name} completed" if success else f"{tool_name} FAILED: {result_str[:100]}",
})
```

### 1.3 Tool-Call-or-Nothing Rule (`agents/prompts.py`)

**Change**: Add an explicit rule to the system prompt that the agent MUST NOT describe an action as completed unless a corresponding tool_call result exists in the conversation.

```
## VERIFICATION MANDATE
- You MUST call a tool to perform ANY real-world action.
- NEVER say an action "has been completed" without a tool_call result.
- If you are unsure whether the last action succeeded, call the Snapshot tool
  IMMEDIATELY and inspect the result before continuing.
- A tool returning any text containing "Error", "Failed", or "not found" 
  means the step DID NOT succeed. Retry or report.
```

### 1.4 Validator Evidence Check (`agents/validator.py`)

**Change**: Before attempting vision validation, check if the most recent tool result already contains a definitive success or failure signal. Skip the expensive vision call if the answer is clear from text alone.

```python
def _check_text_evidence(tool_results: list[dict]) -> bool | None:
    """
    Returns True/False if the text result is conclusive, None if ambiguous.
    Called BEFORE vision validation to save API calls.
    """
    if not tool_results:
        return None
    recent = tool_results[-1]
    result_text = recent.get("result_evidence", recent.get("result", ""))
    
    # Explicit success signals
    if any(s in result_text.lower() for s in [
        "successfully", "completed", "sent", "created", "opened", "saved",
        "launched", "message id:", "event created", "updated"
    ]):
        return True
    
    # Explicit failure signals
    if any(f in result_text.lower() for f in [
        "error:", "failed:", "exception", "access denied", "not found"
    ]):
        return False
    
    return None  # Ambiguous — use vision
```

---

## Phase 2 — Coordinate-Accurate Vision Loop

**Priority**: P0 — Without this, all GUI tasks are unreliable.

### 2.1 Screen Calibration Node (NEW: `agents/calibrator.py`)

This node runs **once per task** before any GUI execution.

```python
# agents/calibrator.py  — NEW FILE

async def calibrator_node(state: AgentState, mcp_client) -> dict:
    """
    Capture screen resolution and DPI scale factor.
    Stores in state so all coordinate calculations are consistent.
    """
    # 1. Get resolution via Windows MCP PowerShell
    ps_cmd = "[System.Windows.Forms.Screen]::PrimaryScreen | Select-Object -ExpandProperty Bounds | Select-Object Width,Height | ConvertTo-Json"
    result = await mcp_client.call_tool("windows", "PowerShell", {"command": ps_cmd})
    
    # 2. Get DPI scale factor
    dpi_cmd = "Add-Type -AssemblyName System.Windows.Forms; [System.Drawing.Graphics]::FromHwnd(0).DpiX"
    dpi_result = await mcp_client.call_tool("windows", "PowerShell", {"command": dpi_cmd})
    
    # 3. Parse and store
    # ... parse JSON from result ...
    
    return {
        "screen_width": width,
        "screen_height": height,
        "dpi_scale": dpi / 96.0,  # 96 DPI = 1.0 scale
        "calibrated": True,
    }
```

**Add to `AgentState`:**
```python
screen_width: int        # Primary monitor width in pixels
screen_height: int       # Primary monitor height in pixels  
dpi_scale: float         # DPI scale factor (1.0 = 96 DPI, 1.25 = 120 DPI, etc.)
calibrated: bool         # Whether calibration has run for this task
```

### 2.2 Vision-Guided Coordinate Finder (`agents/validator.py`)

Replace naive "did it succeed?" with a structured coordinate-extraction prompt.

```python
# In validator.py, add:

_COORDINATE_PROMPT = """
You are a GUI coordinate extractor for Windows automation.
You receive a screenshot and a description of a UI element to interact with.

TASK: Find the CENTER pixel coordinates of the described element.

Screen size: {width}x{height}
Target element: {element_description}

Rules:
1. Return ONLY a JSON object: {{"x": <int>, "y": <int>, "confidence": <0.0-1.0>, "found": <bool>}}
2. x and y must be absolute pixel coordinates from top-left (0,0).
3. If the element is not visible, return {{"found": false, "x": 0, "y": 0, "confidence": 0}}
4. confidence > 0.8 means you are very certain of the coordinates.
"""

async def find_element_coordinates(
    element_description: str,
    screenshot_b64: str,
    screen_width: int,
    screen_height: int,
) -> dict:
    """Returns {"x": int, "y": int, "confidence": float, "found": bool}"""
    ...
```

### 2.3 Two-Phase Click Strategy (`agents/executor.py`)

For any subtask containing action words (click, open, press, type in), enforce a two-phase approach:

**Phase A**: Take Snapshot → Extract coordinates via vision  
**Phase B**: Click at extracted coordinates → Take Snapshot → Verify change

```python
# Logic to add in executor_node before executing GUI subtasks

GUI_ACTION_KEYWORDS = {"click", "press", "open", "launch", "tap", "select", "type in", "fill"}

def _is_gui_task(subtask: str) -> bool:
    return any(kw in subtask.lower() for kw in GUI_ACTION_KEYWORDS)

# If _is_gui_task(current_task):
#   1. Call Snapshot tool
#   2. Call vision LLM with coordinate-extraction prompt
#   3. Pass extracted coordinates to Click tool
#   4. Call Snapshot again for verification
```

### 2.4 Remove All `pyautogui` Dependencies

**Change**: Delete `capture_screen.py`. Replace any pyautogui usage with Windows MCP `Snapshot` + `Click` tools.

```bash
# Identify all pyautogui imports
grep -r "pyautogui" . --include="*.py"

# Remove from requirements.txt
# Remove capture_screen.py entirely
# Replace with Windows MCP native tools
```

**Rationale**: pyautogui uses a different coordinate system than Windows MCP. Using both causes drift. Windows MCP click tools use Win32 `SetCursorPos` + `mouse_event` which is DPI-aware.

---

## Phase 3 — Planner Overhaul (Physical Decomposition)

**Priority**: P1 — Required for complex tasks to work.

### 3.1 Physical Decomposition Enforcer (`agents/planner.py`)

Replace the current planner prompt with a **Physical Action Grammar** that forces the model to think in terms of observable OS events.

**Add to `agents/prompts.py`:**

```python
def build_physical_planner_prompt(screen_width: int, screen_height: int) -> str:
    return f"""
You are a PHYSICAL TASK DECOMPOSER for Windows GUI automation.
Screen resolution: {screen_width}x{screen_height}

## PHYSICAL ACTION GRAMMAR
Every subtask must be ONE of these atomic action types:
  LAUNCH <app_name>                     → use App tool
  SNAPSHOT                              → use Snapshot tool  
  FIND <element_description>            → use vision on latest snapshot
  CLICK_AT <element_description>        → snapshot → find → click coords
  TYPE_TEXT "<text>" IN <element>       → find element → click → type
  PRESS_KEY <key_combo>                 → use keyboard tool
  RUN_POWERSHELL "<command>"            → use PowerShell tool
  READ_FILE <path>                      → use filesystem tool
  WRITE_FILE <path> "<content>"         → use filesystem tool
  VERIFY <expected_visual_state>        → snapshot + vision check
  WAIT_FOR <condition>                  → loop snapshot until condition

## FORBIDDEN SUBTASK PATTERNS (these cause hallucination):
  ✗ "Navigate to the settings"         → too vague, what does "navigate" mean physically?
  ✗ "Set up the interface"             → not atomic
  ✗ "Use the browser"                  → not a single action
  ✗ "Open the file"                    → OK only if you specify LAUNCH or READ_FILE

## EXAMPLE — "Find the top Python repos on GitHub":
  1. LAUNCH browser (Chrome or Edge)
  2. SNAPSHOT (verify browser opened, get address bar location)
  3. CLICK_AT address bar
  4. TYPE_TEXT "https://github.com/trending/python" IN address bar
  5. PRESS_KEY Enter
  6. SNAPSHOT (verify page loaded, get repo list area)
  7. FIND trending repository list
  8. READ visible text from snapshot (extract repo names)

Output ONLY a JSON object with "subtasks": [list of strings using the grammar above].
"""
```

### 3.2 Dependency Validation (`agents/planner.py`)

After generating the checklist, validate that:
- FIND always follows a SNAPSHOT
- CLICK_AT always has something to click (preceded by FIND or SNAPSHOT)
- VERIFY is the last step of any sequence

```python
def _validate_checklist_dependencies(subtasks: list[str]) -> list[str]:
    """
    Inject missing prerequisite steps.
    E.g. if CLICK_AT appears without a prior SNAPSHOT, inject one.
    """
    validated = []
    last_snapshot_idx = -1
    
    for i, step in enumerate(subtasks):
        step_upper = step.upper()
        
        # Auto-inject SNAPSHOT before FIND or CLICK_AT if missing
        if ("FIND " in step_upper or "CLICK_AT" in step_upper):
            if last_snapshot_idx < i - 1:
                validated.append("SNAPSHOT (verify current screen state)")
                last_snapshot_idx = len(validated) - 1
        
        if "SNAPSHOT" in step_upper:
            last_snapshot_idx = len(validated)
            
        validated.append(step)
    
    return validated
```

### 3.3 Max Retries Per Subtask With Escalation

Currently all retries use the same approach. Add escalation:

| Attempt | Strategy |
|---|---|
| 1 | Try as planned |
| 2 | Take fresh Snapshot → re-extract coordinates → retry |
| 3 | Try alternative approach (e.g., keyboard shortcut instead of mouse click) |
| 4 | Mark failed, continue with remaining subtasks, report partial completion |

---

## Phase 4 — Windows MCP Full Utilization

**Priority**: P1

### 4.1 Tool Selector Map (`agents/prompts.py`)

Add an explicit action→tool routing table to the system prompt:

```
## WINDOWS MCP TOOL SELECTOR (use this as a lookup table)

| I want to...                    | Use this tool           | Args needed              |
|---------------------------------|-------------------------|--------------------------|
| See current screen              | Snapshot                | {}                       |
| Click a visible element         | Click                   | x, y (from vision)       |
| Type text into focused field    | Type                    | text                     |
| Press a keyboard shortcut       | KeyPress                | key (e.g. "ctrl+c")      |
| Open an application             | App → launch            | name or path             |
| Close an application            | App → close             | name or pid              |
| Run a shell command             | PowerShell              | command (string)         |
| Read a file                     | FileSystem → read       | path                     |
| Write/create a file             | FileSystem → write      | path, content            |
| List directory contents         | FileSystem → list       | path                     |
| Copy text to clipboard          | Clipboard → set         | text                     |
| Read clipboard content          | Clipboard → get         | {}                       |
| Get running processes           | Process → list          | {}                       |
| Kill a process                  | Process → kill          | pid or name              |
| Show a notification             | Notification → send     | title, body              |
| Read a registry key             | Registry → get          | key_path                 |
| Scroll the screen               | Scroll                  | x, y, direction, amount  |
| Move the mouse cursor           | Move                    | x, y                     |
```

### 4.2 Accessibility API Integration (New Tool)

Windows MCP may expose UI Automation. Add a wrapper tool that uses `UIAutomation` via PowerShell to find elements by accessibility name — far more reliable than pixel coordinates.

```python
# New tool to add in tools/windows_enhanced_tools.py

@tool
async def find_ui_element(element_name: str, element_type: str = "Button") -> str:
    """
    Find a UI element by accessibility name using Windows UI Automation.
    More reliable than pixel coordinate guessing.
    Args:
        element_name: The AutomationId, Name, or text of the element.
        element_type: ControlType (Button, Edit, MenuItem, etc.)
    Returns:
        JSON with {found: bool, x: int, y: int, bounds: {...}}
    """
    ps_command = f"""
    Add-Type -AssemblyName UIAutomationClient
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, "{element_name}"
    )
    $el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
    if ($el) {{
        $rect = $el.Current.BoundingRectangle
        @{{found=$true; x=[int]($rect.X + $rect.Width/2); y=[int]($rect.Y + $rect.Height/2)}} | ConvertTo-Json
    }} else {{
        @{{found=$false}} | ConvertTo-Json
    }}
    """
    # Call via Windows MCP PowerShell tool
    ...
```

### 4.3 Context-Aware Tool Pre-selection (`agents/supervisor.py`)

Before routing, detect the task domain and pre-tag it so the executor knows which MCP server to prefer:

```python
DOMAIN_HINTS = {
    "browser": ["playwright"],
    "file": ["windows"],
    "email": ["google"],
    "calendar": ["google"],
    "click|type|screenshot|window": ["windows"],
    "stock|price|sec|edgar|filing": ["fincept"],
}

# Add "mcp_hint" to state: preferred MCP server for this task
```

---

## Phase 5 — Orion UI Shell (Floating Chat + Log Viewer)

**Priority**: P1 — User's explicit request.

### 5.1 Architecture

A FastAPI server (`ui/server.py`) serves:
- `GET /` → the Orion UI HTML page
- `WebSocket /ws/chat` → bidirectional chat messages
- `WebSocket /ws/logs` → streaming log tail
- `POST /api/message` → submit a message (fallback for non-WS)

The page is a single HTML file with:
- **Floating bubble** in bottom-right corner (48×48px circle, branded)
- **Expand/collapse** on click → opens a 380×520px panel
- **Chat panel** — scrollable message history + input bar at bottom
- **Log tab** — live tail of the agent log file with color-coded levels
- **Status dot** — green/yellow/red showing agent state

### 5.2 File Layout

```
ui/
├── server.py          # FastAPI app + WebSocket handlers
├── log_streamer.py    # Tails the log file and streams via WebSocket
├── static/
│   └── orion.html     # Single-file UI (HTML + CSS + JS inline)
└── __init__.py
```

### 5.3 `ui/server.py` Implementation Outline

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import asyncio, json
from pathlib import Path

app = FastAPI()

# Active WebSocket connections
chat_connections: list[WebSocket] = []
log_connections: list[WebSocket] = []

_agent_graph = None  # Set by main.py at startup

@app.websocket("/ws/chat")
async def chat_ws(ws: WebSocket):
    await ws.accept()
    chat_connections.append(ws)
    try:
        while True:
            data = await ws.receive_text()
            payload = json.loads(data)
            user_msg = payload.get("message", "")
            
            # Broadcast user message back immediately
            await _broadcast_chat({"role": "user", "content": user_msg})
            
            # Run through agent graph
            from langchain_core.messages import HumanMessage
            state = {"messages": [HumanMessage(content=user_msg)], "user_id": "ui_user"}
            result = await _agent_graph.ainvoke(state, config={"configurable": {"thread_id": "ui_session"}})
            
            # Stream response back
            response = result["messages"][-1].content
            await _broadcast_chat({"role": "assistant", "content": response})
    except WebSocketDisconnect:
        chat_connections.remove(ws)

@app.websocket("/ws/logs")
async def log_ws(ws: WebSocket):
    await ws.accept()
    log_connections.append(ws)
    try:
        # Tail the log file
        async for line in tail_log_file():
            await ws.send_text(json.dumps({"line": line}))
    except WebSocketDisconnect:
        log_connections.remove(ws)

async def _broadcast_chat(msg: dict):
    for ws in list(chat_connections):
        try:
            await ws.send_text(json.dumps(msg))
        except:
            chat_connections.remove(ws)
```

### 5.4 UI Design Specification

**Visual design**: Dark theme. Space-tech aesthetic matching "Orion" name.

**Floating bubble (collapsed)**:
- 52×52px circle, bottom-right, 20px from edges
- Background: `#0a0f1e` (deep navy)
- Orion constellation SVG icon (white lines connecting 7 stars)
- Pulse animation when agent is processing
- Red badge counter for unread messages
- `position: fixed; z-index: 9999`

**Expanded panel (380×520px)**:
- Slides up with spring easing from bubble
- Header: "ORION" in monospace, status dot, minimize button, settings icon
- **Chat tab**: bubble-style messages, typing indicator, input with send button
- **Logs tab**: monospace font, color-coded lines (INFO=cyan, WARNING=yellow, ERROR=red), auto-scroll with pause-on-hover
- Keyboard shortcut: `Ctrl+Space` to toggle

**Status dot colors**:
- 🟢 Green: idle / done
- 🟡 Yellow (pulsing): executing / thinking
- 🔴 Red: error

### 5.5 `main.py` Integration

Add to the launch sequence:

```python
# In main() — add UI server as a background task
if config.UI_ENABLED:  # New env var: UI_ENABLED=true
    from ui.server import app as ui_app, set_graph
    import uvicorn
    
    set_graph(agent_graph)
    
    ui_server = uvicorn.Server(uvicorn.Config(
        ui_app,
        host="127.0.0.1",
        port=int(config.UI_PORT),  # New env var: UI_PORT=8765
        log_level="warning",
    ))
    tasks.append(asyncio.create_task(ui_server.serve()))
    
    logger.info("Orion UI running at http://127.0.0.1:%s", config.UI_PORT)
    # Auto-open in default browser on startup
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{config.UI_PORT}")
```

### 5.6 New Environment Variables

```env
# Add to .env.example
UI_ENABLED=true
UI_PORT=8765
UI_AUTO_OPEN=true
LOG_FILE_PATH=./logs/orion.log
```

---

## Phase 6 — Memory & Context Improvements

**Priority**: P2

### 6.1 Task Fingerprinting

Before running any task, hash the task description and check SQLite. If same task ran successfully in last 24h, offer the cached result.

```python
import hashlib

def _task_fingerprint(task: str) -> str:
    return hashlib.sha256(task.lower().strip().encode()).hexdigest()[:16]
```

### 6.2 Subtask-Level Memory

Currently memory stores whole tasks. Change to store subtask→result pairs so future planners can reuse proven action sequences.

```python
# In memory/vector_store.py — new function
async def embed_subtask_result(subtask: str, tool_name: str, args: dict, result: str, success: bool):
    """Store individual subtask outcomes for planner reuse."""
    ...
```

### 6.3 Failure Pattern Learning

Log failed subtasks with their error messages to a separate `failures` ChromaDB collection. Before planning, query this collection and add a "known failures" section to the planner context.

---

## File-by-File Change Map

| File | Change Type | Priority |
|---|---|---|
| `agents/executor.py` | Modify — add failure detection, result evidence | P0 |
| `agents/validator.py` | Modify — text evidence check, coordinate-aware | P0 |
| `agents/prompts.py` | Modify — tool selector table, verification mandate | P0 |
| `agents/planner.py` | Modify — physical grammar, dependency validation | P1 |
| `agents/calibrator.py` | CREATE NEW — screen calibration node | P0 |
| `agents/state.py` | Modify — add screen_width, screen_height, dpi_scale | P0 |
| `agents/graph.py` | Modify — add calibrator node, wire UIBridge | P1 |
| `tools/windows_enhanced_tools.py` | CREATE NEW — UI automation, element finder | P1 |
| `ui/server.py` | CREATE NEW — FastAPI WebSocket server | P1 |
| `ui/log_streamer.py` | CREATE NEW — log file tailer | P1 |
| `ui/static/orion.html` | CREATE NEW — floating chat UI | P1 |
| `ui/__init__.py` | CREATE NEW | P1 |
| `config.py` | Modify — add UI_ENABLED, UI_PORT, LOG_FILE_PATH | P1 |
| `.env.example` | Modify — add new vars | P1 |
| `main.py` | Modify — add UI server task, pass log path to logger | P1 |
| `utils/logger.py` | Modify — add file handler, expose log path | P1 |
| `memory/vector_store.py` | Modify — subtask-level embedding | P2 |
| `memory/task_history.py` | Modify — failure pattern logging | P2 |
| `capture_screen.py` | DELETE — replaced by Windows MCP Snapshot | P0 |
| `requirements.txt` | Modify — add fastapi, uvicorn, websockets | P1 |

---

## Testing Checklist

### Phase 1 (Anti-Hallucination)
- [ ] Run `"open Notepad"` — verify tool_results shows `success: true` AND Notepad actually opens
- [ ] Simulate a tool returning `"Error: access denied"` — verify executor marks `success: false`
- [ ] Run task when Windows MCP is disconnected — verify error propagates cleanly to responder

### Phase 2 (Coordinate Accuracy)
- [ ] On a 1920×1080 display at 100% scaling — click accuracy within 5px
- [ ] On a 2560×1440 display at 150% scaling — click accuracy within 10px
- [ ] Test accessibility API path: `find_ui_element("Close", "Button")` on Notepad

### Phase 3 (Planner)
- [ ] `"Find top Python repos on GitHub"` — verify 6-8 PHYSICAL steps in checklist
- [ ] `"Send an email summarizing today's calendar"` — verify cross-tool sequence
- [ ] Retry escalation: simulate click missing target → verify attempt 2 re-extracts coords

### Phase 4 (Windows MCP)
- [ ] Every action in the tool selector table executes at least once per test run
- [ ] `find_ui_element` returns valid coordinates for visible UI elements

### Phase 5 (UI)
- [ ] Floating bubble appears in bottom-right on browser open
- [ ] Chat message round-trips through agent and returns response
- [ ] Log tab auto-scrolls and color-codes INFO/WARNING/ERROR
- [ ] `Ctrl+Space` toggles panel
- [ ] Status dot turns yellow during execution, green on completion

---

## Dependency Additions

```txt
# Add to requirements.txt
fastapi==0.115.0
uvicorn[standard]==0.30.6
websockets==13.1          # already present — confirm version
aiofiles==23.2.1          # async file reading for log tailer
pywin32==306              # Windows UI Automation via win32com
```

```bash
# Remove from requirements.txt (no longer needed)
pyautogui                 # Replaced by Windows MCP native tools
```

---

*Implementation Plan v1.0 — Review before starting Phase 1.*
