"""LangGraph ↔ Backchannel demo.

A LangGraph subgraph fans work out to N workers via a broadcast channel,
waits until each worker drops an ack on a sibling 'results' channel,
then aggregates. The two halves can run as completely separate processes.

For brevity, this file simulates both halves in one process. In production,
the 'worker' loop would run in a separate container, k8s pod, or even a
different cloud — the only contract is the Backchannel URL.

Run:

    pip install langgraph backchannel
    python graph_demo.py
"""

from __future__ import annotations

import os
import sys
import time
import uuid

try:
    from backchannel_sdk import BackchannelClient  # type: ignore
except ImportError:
    print(
        "This demo needs the Backchannel Python SDK:\n"
        "    pip install backchannel\n"
        "(or `pip install -e ../../sdk/python` against this repo)",
        file=sys.stderr,
    )
    sys.exit(1)

BASE = os.environ.get("BACKCHANNEL_BASE_URL", "https://backchannel.oakstack.eu")
WORK = f"lg-work-{uuid.uuid4().hex[:6]}"
RESULTS = f"{WORK}-results"


def mint(label: str) -> BackchannelClient:
    bootstrap = BackchannelClient(api_key="", base_url=BASE)
    issued = bootstrap.issue_key(agent_label=label)
    return BackchannelClient(api_key=issued["key"], base_url=BASE)


# --- Producer (would be a LangGraph node) --------------------------------


def fan_out(client: BackchannelClient, tasks: list[str]) -> list[str]:
    """Post N tasks on a claimable channel; return message ids."""
    client.create_channel(name=WORK, mode="claimable")
    client.create_channel(name=RESULTS, mode="broadcast")
    ids = []
    for task in tasks:
        env = client.post_message(WORK, content=task, actor_label="orchestrator")
        ids.append(env["message"]["id"])
    return ids


def await_results(client: BackchannelClient, expected: int, timeout: float = 30.0) -> list[str]:
    """Pull results off the broadcast channel until we have `expected` of them."""
    results: list[str] = []
    since = None
    deadline = time.time() + timeout
    while len(results) < expected and time.time() < deadline:
        page = client.list_messages(RESULTS, since=since, limit=50)
        for msg in page["data"]:
            results.append(msg["content"])
        since = page.get("next_cursor") or since
        if len(results) < expected:
            time.sleep(0.5)
    return results


# --- Worker loop (would be a separate process / container) ----------------


def worker_loop(client: BackchannelClient, worker_name: str, max_msgs: int = 5) -> None:
    actor = client.create_actor(name=worker_name)
    processed = 0
    while processed < max_msgs:
        page = client.list_messages(WORK, limit=20)
        any_claimed = False
        for msg in page["data"]:
            if msg.get("status") == "claimed" or msg.get("acknowledged_by"):
                continue
            try:
                client.claim_message(msg["id"], actor=actor["id"])
            except Exception:
                continue  # someone else got it
            client.post_message(
                RESULTS,
                content=f"{worker_name} processed: {msg['content']}",
                actor_label=worker_name,
            )
            client.ack_message(msg["id"], actor=actor["id"])
            processed += 1
            any_claimed = True
        if not any_claimed:
            return


# --- Main -----------------------------------------------------------------


def main() -> None:
    orchestrator = mint(f"lg-orch-{uuid.uuid4().hex[:6]}")
    workers = [mint(f"lg-worker-{i}-{uuid.uuid4().hex[:4]}") for i in range(3)]

    tasks = [f"analyze section-{i}" for i in range(6)]
    print(f"▸ orchestrator fanning out {len(tasks)} tasks on '{WORK}'")
    fan_out(orchestrator, tasks)

    print("▸ 3 workers drain the queue (would be 3 separate processes)")
    for w in workers:
        worker_loop(w, worker_name=f"worker-{workers.index(w)+1}", max_msgs=len(tasks))

    print("▸ orchestrator awaits results on broadcast channel")
    results = await_results(orchestrator, expected=len(tasks))
    for r in results:
        print(f"  · {r}")
    print(f"\n✓ collected {len(results)}/{len(tasks)} results")


if __name__ == "__main__":
    main()
