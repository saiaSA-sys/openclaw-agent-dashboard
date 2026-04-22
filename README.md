# OpenClaw Agent OS Dashboard

A premium dashboard for monitoring OpenClaw agent activity, model usage, and task management.

## 🚀 Features

- **Real-time Model Monitoring** - Track Claude, Gemini, Gemma, and Qwen usage
- **Session Activity Feed** - Live view of recent agent actions and tools
- **Task Management** - Kanban board for project tracking
- **Usage Analytics** - Model distribution and performance metrics
- **Multi-Agent Insights** - Monitor different agent personas and roles

## 🏠 Local Installation

This dashboard is designed to run **locally alongside your OpenClaw instance** and reads real session data.

### Prerequisites
- OpenClaw installation with active session logs
- Python 3.8+
- Access to OpenClaw workspace directories

### Setup
1. Clone this repository
2. Place in your OpenClaw workspace (or update paths in server.py)
3. Run the dashboard server:
   ```bash
   python3 server.py
   ```
4. Open http://localhost:8080

## 🔒 Security Note

This dashboard reads local OpenClaw session files and configuration. It's designed for **local development use only** and should not be exposed to public networks.

## 📁 Data Sources

- Session logs: `~/.openclaw/agents/*/sessions/*.jsonl`
- Configuration: `~/.openclaw/openclaw.json`
- Tasks: `board-tasks.json` (in workspace)

## 🎯 Use Cases

- Monitor agent performance and activity
- Debug model routing and usage patterns  
- Track project tasks and progress
- Analyze OpenClaw system health

Built for OpenClaw power users who need deep insights into their agent operations.