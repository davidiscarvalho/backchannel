# Backchannel + CrewAI demo

Two CrewAI agents that don't share memory or process coordinate via
Backchannel: producer posts, worker claims, producer awaits ack.

```bash
pip install crewai backchannel  # or `pip install -e ../../sdk/python` against this repo
python crew_demo.py
```

What you see (~3 seconds):

```
▸ producer creates claimable channel 'crewai-research-abc123'
▸ producer posts a task
  message id: msg_…
▸ worker drains channel and claims the task
  claimed by: claimer-1
▸ worker 'does the work' (echo for demo) and acks
▸ producer awaits ack
  ✓ acknowledged by claimer-1
```

## Wiring into real CrewAI agents

Replace the `worker.ack_message(...)` line with a `Task.execute()` call,
have the agent post the result on a sibling `f"{CHANNEL}-results"`
channel, and the producer can `subscribe(f"{CHANNEL}-results")` instead
of polling for an ack.

This is the result-channel pattern that becomes a first-class API in
Phase B2 (`await_result`). Until then, the pattern lives here.
