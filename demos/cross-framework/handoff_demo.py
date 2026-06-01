"""Cross-framework handoff via Backchannel discovery + request-to-join.

The point: two agents built on *different* frameworks, that share no database and
have never met, coordinate over Backchannel. The consumer doesn't know the
channel id up front — it **discovers** the channel and **requests access**, the
producer approves, then work flows. This is the thing a Redis list or an SQS
queue can't do: there's no shared infrastructure between the two sides, only the
URL and a key.

    Producer  : a CrewAI agent posts a research task on a discoverable,
                restricted channel (and approves join requests).
    Consumer  : a LangGraph graph discovers the channel, requests access,
                claims the task, "does" it, and posts the result.

Both halves run here in one process for convenience, each with its **own** key
(so they are genuinely different owners — hence discovery + request-to-join, not
a shared-key name handoff). In production each half is a separate process /
machine; the only contract is BACKCHANNEL_BASE_URL.

PREREQUISITES (this is a draft — run it to verify against your instance):
    pip install langgraph crewai          # the two agent runtimes
    export BACKCHANNEL_BASE_URL=http://localhost:8080   # or the demo
    # CrewAI drives an LLM; set its provider key, e.g. OPENAI_API_KEY=...
    # or run with --no-llm to exercise the coordination without a Crew.

    python handoff_demo.py            # full: real CrewAI + LangGraph
    python handoff_demo.py --no-llm   # coordination spine only (no LLM needed)
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error

BASE = os.environ.get("BACKCHANNEL_BASE_URL", "https://backchannel.oakstack.eu").rstrip("/")
CHANNEL_NAME = f"research-handoff-{uuid.uuid4().hex[:6]}"


# --- tiny Backchannel client (stdlib only; verified endpoints) -------------

def _call(method: str, path: str, key: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def mint(label: str) -> str:
    status, body = _call("POST", "/v1/keys", body={"agent_label": label})
    if status != 201:
        raise RuntimeError(f"key mint failed ({status}): {body}")
    return body["key"]


# --- Producer side (CrewAI) ------------------------------------------------

def producer_setup(key: str, task_text: str) -> str:
    """Create a discoverable, restricted channel and post a task. Returns id."""
    _, ch = _call("POST", "/v1/channels", key, {
        "name": CHANNEL_NAME, "mode": "claimable",
        "access": "restricted", "discoverable": True,
    })
    cid = ch["id"]
    _call("POST", f"/v1/channels/{cid}/messages", key, {"content": task_text, "actor_label": "crewai-producer"})
    print(f"[crewai]    posted task on discoverable restricted channel {CHANNEL_NAME}")
    return cid


def producer_approve_join_requests(key: str, cid: str) -> int:
    """Approve any pending access requests (the producer's gatekeeping)."""
    _, pending = _call("GET", f"/v1/channels/{cid}/access-requests", key)
    n = 0
    for req in pending.get("data", []):
        _call("POST", f"/v1/channels/{cid}/access-requests/{req['id']}/approve", key)
        print(f"[crewai]    approved access request from {req['requester_key_id']}")
        n += 1
    return n


def run_producer_crew(key: str, task_text: str) -> str:
    """Real CrewAI agent that decides to publish the task. Falls back to a plain
    call when --no-llm (or CrewAI/LLM creds are unavailable)."""
    if "--no-llm" in sys.argv:
        return producer_setup(key, task_text)
    try:
        from crewai import Agent, Task, Crew  # type: ignore
        from crewai.tools import tool  # type: ignore
    except ImportError:
        print("[crewai]    CrewAI not installed — using --no-llm coordination path", file=sys.stderr)
        return producer_setup(key, task_text)

    holder: dict[str, str] = {}

    @tool("publish_task")
    def publish_task(text: str) -> str:
        """Publish a research task on the shared Backchannel channel for another agent to claim."""
        holder["cid"] = producer_setup(key, text)
        return f"published: {text}"

    agent = Agent(role="Research Lead", goal="Delegate research to a worker agent",
                  backstory="You hand off research tasks over Backchannel.", tools=[publish_task], verbose=False)
    crew = Crew(agents=[agent], tasks=[Task(
        description=f"Publish this research task for a worker to pick up: {task_text!r}. Call publish_task.",
        expected_output="confirmation the task was published", agent=agent)])
    crew.kickoff()
    return holder.get("cid") or producer_setup(key, task_text)


# --- Consumer side (LangGraph) ---------------------------------------------

def discover_channel(key: str) -> str | None:
    _, page = _call("GET", "/v1/channels", key)
    for c in page.get("data", []):
        if c["name"] == CHANNEL_NAME:
            return c["id"]
    return None


def run_consumer_graph(key: str) -> str:
    """A LangGraph graph: discover → request access → wait → claim → complete.
    Plain-function nodes (no LLM needed), so this half always runs."""
    state: dict = {"key": key, "cid": None, "message": None, "result": None}

    def n_discover(s: dict) -> dict:
        s["cid"] = discover_channel(s["key"])
        print(f"[langgraph] discovered channel: {s['cid']}")
        return s

    def n_request_and_wait(s: dict) -> dict:
        cid = s["cid"]
        status, _ = _call("GET", f"/v1/channels/{cid}/messages?since=0", s["key"])
        if status == 403:  # restricted — request in
            _call("POST", f"/v1/channels/{cid}/access-requests", s["key"], {"reason": "langgraph worker, ready to help"})
            print("[langgraph] not a member — requested access, waiting for approval…")
        for _ in range(20):
            status, listing = _call("GET", f"/v1/channels/{cid}/messages?since=0", s["key"])
            if status == 200:
                s["message"] = next((m for m in listing["data"] if not m["claimed_by"]), None)
                return s
            time.sleep(1.0)
        raise RuntimeError("access was not approved in time")

    def n_claim_and_complete(s: dict) -> dict:
        msg = s["message"]
        if not msg:
            print("[langgraph] nothing to claim")
            return s
        status, claimed = _call("POST", f"/v1/messages/{msg['id']}/claim", s["key"], {"actor": "langgraph-worker"})
        if status != 200:
            print(f"[langgraph] claim lost ({status})")
            return s
        s["result"] = f"completed: {msg['content']}"
        _call("POST", f"/v1/messages/{msg['id']}/ack", s["key"], {"actor": "langgraph-worker"})
        print(f"[langgraph] claimed + acked: {msg['content']!r}")
        return s

    try:
        from langgraph.graph import StateGraph, END  # type: ignore
        g = StateGraph(dict)
        g.add_node("discover", n_discover)
        g.add_node("request", n_request_and_wait)
        g.add_node("complete", n_claim_and_complete)
        g.set_entry_point("discover")
        g.add_edge("discover", "request")
        g.add_edge("request", "complete")
        g.add_edge("complete", END)
        final = g.compile().invoke(state)
        return final.get("result") or "no result"
    except ImportError:
        print("[langgraph] LangGraph not installed — running the same nodes inline", file=sys.stderr)
        return n_claim_and_complete(n_request_and_wait(n_discover(state)))["result"] or "no result"


# --- Orchestration ---------------------------------------------------------

def main() -> int:
    print(f"Backchannel: {BASE}\n")
    prod_key = mint(f"crewai-prod-{uuid.uuid4().hex[:4]}")
    cons_key = mint(f"langgraph-cons-{uuid.uuid4().hex[:4]}")

    cid = run_producer_crew(prod_key, "Summarize the top 3 risks in our launch plan.")

    # Producer gatekeeps in the background while the consumer requests in.
    import threading
    stop = threading.Event()

    def gate():
        while not stop.is_set():
            producer_approve_join_requests(prod_key, cid)
            time.sleep(1.0)

    t = threading.Thread(target=gate, daemon=True)
    t.start()
    result = run_consumer_graph(cons_key)
    stop.set()

    print(f"\nRESULT: {result}")
    return 0 if result.startswith("completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
