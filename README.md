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
- **Physical Sensory-Motor Loop**: Full desktop automation via vision and coordinate-based clicks.
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

Orion comes with a unified launcher (`orion.bat` or `run.bat`) that handles virtual environment activation and sets up the correct paths automatically.

**Run the standard CLI interface:**
```bash
.\orion.bat cli
```

**Run via Telegram Bot:**
```bash
.\orion.bat telegram
```

**Run via Slack Bot:**
```bash
.\orion.bat slack
```

---
*Powered by the advanced LangGraph framework and NVIDIA NIM reasoning engines.*
