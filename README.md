# **Agent Orion**

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-FF9900?style=for-the-badge&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)
![Qt](https://img.shields.io/badge/Qt-41CD52?style=for-the-badge&logo=qt&logoColor=white)
![C++](https://img.shields.io/badge/C++-00599C?style=for-the-badge&logo=c%2B%2B&logoColor=white)
![Slack](https://img.shields.io/badge/Slack-4A154B?style=for-the-badge&logo=slack&logoColor=white)

Agent Orion is a highly robust AI agent with native Windows integration, physical sensory-motor loops for GUI automation, and a powerful Multi-MCP architecture. Orion can interface with desktop applications, browsers via Playwright, and financial data tools like Fincept Terminal.

## 🚀 Features
- **Multi-MCP Architecture**: Concurrently runs Windows, Playwright, and Fincept MCP servers.
- **Native Windows Automation**: Uses `tools/input_tools.py` for direct coordinate-based clicks and fast element typing without relying on brittle external libraries like `pyautogui`.
- **High-Speed Execution**: Sub-8s executor latency using optimized token boundaries and a skip-vision pipeline for non-GUI tasks.
- **Three-Way Intent Routing**: Efficiently routes queries into Chat, Simple Tasks, or Complex Planner-based executions.
- **Unified Interfaces**: Communicate with Orion through CLI, Telegram, or Slack.

## 🛠️ Prerequisites
- **Python**: Version 3.10 or higher.
- **Node.js**: Required for Playwright and specific MCP servers (`npx`).
- **Visual Studio Build Tools / CMake**: If building Fincept Terminal from source.

## ⚙️ Setup Guide

1. **Clone the Repository**
   ```bash
   git clone https://github.com/your-username/agent-orion.git
   cd agent-orion
   ```

2. **Create the Virtual Environment**
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   playwright install
   ```

4. **Install MCP Servers (Global)**
   ```bash
   # For Windows MCP
   pip install uv
   uv tool install windows-mcp
   ```

## 📁 Directory Structure
```
AGENT_ORION/
├── agents/         # LangGraph agents (executor, planner, supervisor, validator)
├── docs/           # Project documentation and implementation plans
├── interfaces/     # CLI, Slack, and Telegram bot interfaces
├── memory/         # ChromaDB vector store and task SQLite DB
├── orion-widget/   # Electron frontend widget for Orion
├── tools/          # Native MCP input tools and client wrappers
├── ui/             # WebSockets server for UI integration
└── main.py         # Main application entry point
```

## 🔐 Environment Setup
Copy the example environment file and fill in your API keys:
```bash
copy .env.example .env
```
Ensure the following are set up:
- `NVIDIA_NIM_API_KEY` (for Reasoning/Routing)
- `OPENROUTER_API_KEY` (for Vision tasks)
- `TELEGRAM_BOT_TOKEN` / `SLACK_BOT_TOKEN` (for external interfaces)

## ▶️ Run Commands

Orion comes with a unified launcher (`run.bat`) that handles virtual environment activation and sets up the correct paths automatically.

**Run the standard CLI interface:**
```bash
.\run.bat cli
```

**Run via Telegram Bot:**
```bash
.\run.bat telegram
```

**Run via Slack Bot:**
```bash
.\run.bat slack
```

---
*Powered by the advanced LangGraph framework and NVIDIA NIM reasoning engines.*
