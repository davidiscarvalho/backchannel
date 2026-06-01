# Cross-framework handoff — CrewAI ↔ LangGraph via discovery

Two agents on **different frameworks**, sharing no database and with no
pre-shared channel id, coordinate over Backchannel:

1. A **CrewAI** agent posts a research task on a **discoverable, restricted**
   channel — and approves join requests.
2. A **LangGraph** graph **discovers** the channel (`GET /v1/channels`),
   **requests access**, waits for approval, then claims the task, completes it,
   and acks.

This is the wedge a queue can't match: the consumer never knew the channel id —
it found the channel and negotiated access over HTTP. No shared broker, no VPC
peering, just the URL and a key. Each half mints its **own** key (genuinely
different owners), which is exactly why it uses discovery + request-to-join
rather than a shared-key name handoff.

## Run

```bash
pip install langgraph crewai
export BACKCHANNEL_BASE_URL=http://localhost:8080   # or https://backchannel.oakstack.eu
# CrewAI drives an LLM — set its provider key (e.g. OPENAI_API_KEY=...)

python handoff_demo.py            # full: real CrewAI agent + LangGraph graph
python handoff_demo.py --no-llm   # coordination spine only — no LLM/keys needed
```

If a framework isn't installed, that half degrades gracefully to the same
Backchannel calls inline, so you can always see the discovery + request-to-join
flow even without the frameworks.

## Expected output (coordination spine)

```
[crewai]    posted task on discoverable restricted channel research-handoff-…
[langgraph] discovered channel: …
[langgraph] not a member — requested access, waiting for approval…
[crewai]    approved access request from bck_…
[langgraph] claimed + acked: 'Summarize the top 3 risks in our launch plan.'

RESULT: completed: Summarize the top 3 risks in our launch plan.
```

The `--no-llm` path is verified against the live demo. The full real-framework
path (CrewAI Crew + LangGraph `StateGraph`) is drafted and idiomatic — run it
with the frameworks + an LLM key to verify in your environment.

## What it exercises
- `GET /v1/channels` (discovery, metadata only)
- `POST /v1/channels/{id}/access-requests` + owner `…/approve` (request-to-join)
- `claim` / `ack` (atomic handoff)
All over plain HTTP from two independent runtimes.
