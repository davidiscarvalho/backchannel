"""CrewAI ↔ Backchannel demo.

One CrewAI agent posts a research task on a claimable Backchannel channel.
A second agent (could be in a different process, different machine) claims
it, "researches" (we just echo), and acks. The producer awaits the result.

The point: CrewAI agents that don't share memory can still coordinate
durably, with no Postgres/Redis/queue to operate.

Run:

    pip install crewai
    pip install -e ../../sdk/python   # the Backchannel SDK (not yet on PyPI)
    python crew_demo.py
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
        "This demo needs the Backchannel Python SDK. Install with:\n"
        "    pip install -e ../../sdk/python   # (not yet on PyPI)\n"
        "(while this repo still uses the in-tree SDK, run: pip install -e ./sdk/python)",
        file=sys.stderr,
    )
    sys.exit(1)

BASE = os.environ.get("BACKCHANNEL_BASE_URL", "https://backchannel.oakstack.eu")
CHANNEL = f"crewai-research-{uuid.uuid4().hex[:6]}"


def make_client(label: str) -> BackchannelClient:
    """Mint a fresh key per agent — production code would reuse a stored key."""
    bootstrap = BackchannelClient(api_key="", base_url=BASE)
    issued = bootstrap.issue_key(agent_label=label)
    return BackchannelClient(api_key=issued["key"], base_url=BASE)


def run() -> None:
    producer = make_client(f"crew-producer-{uuid.uuid4().hex[:6]}")
    worker = make_client(f"crew-worker-{uuid.uuid4().hex[:6]}")

    print(f"▸ producer creates claimable channel '{CHANNEL}'")
    channel = producer.create_channel(name=CHANNEL, mode="claimable")
    cid = channel["id"]

    print("▸ producer posts a task")
    posted = producer.send_message(
        cid,
        content="Summarize the latest CrewAI release notes in three bullets.",
        actor_label="researcher-orchestrator",
    )
    msg_id = posted["id"]
    print(f"  message id: {msg_id}")

    print("▸ worker drains channel and claims the task")
    page = worker.list_messages(cid, limit=10)
    target = next(m for m in page["data"] if m["id"] == msg_id)
    # A plain actor name auto-creates the actor on claim — no pre-registration.
    claim = worker.claim_message(target["id"], actor="claimer-1")
    print(f"  claimed by: {claim['message']['claimed_by']['name']}")

    print("▸ worker 'does the work' (echo for demo) and acks")
    # In a real CrewAI run, this is where you'd dispatch to a Task / Agent /
    # Tool, capture the result, and write it back on a sibling channel
    # named e.g. f"{CHANNEL}-results".
    worker.ack_message(target["id"], actor="claimer-1")

    print("▸ producer awaits ack")
    for _ in range(20):
        page = producer.list_messages(cid, limit=10)
        match = next((m for m in page["data"] if m["id"] == msg_id), None)
        if match and match.get("acknowledged_by"):
            print(f"  ✓ acknowledged by {match['acknowledged_by'][0]['name']}")
            break
        time.sleep(0.5)
    else:
        print("  ✗ timed out waiting for ack")
        sys.exit(2)


if __name__ == "__main__":
    run()
