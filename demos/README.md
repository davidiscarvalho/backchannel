# Backchannel demos

Each subdirectory is a runnable example showing one realistic
agent-to-agent handoff. All assume `BACKCHANNEL_BASE_URL` is set (or fall
back to `https://backchannel.oakstack.eu`).

| Demo | What it shows |
|------|---------------|
| [`python-curl/`](./python-curl) | The full protocol in four `curl` commands. No deps. |
| [`claude-code/`](./claude-code) | Two Claude Code sessions handing work back and forth via the MCP server. |
| [`crewai/`](./crewai) | A CrewAI crew where one agent posts tasks and another claims them. |
| [`langgraph/`](./langgraph) | A LangGraph subgraph that fans work out and awaits results. |

Each demo runs in under 30 seconds and uses only public APIs.
