# Backchannel + LangGraph demo

LangGraph orchestrator fans 6 tasks out to 3 workers via a claimable
Backchannel channel; workers post results on a sibling broadcast
channel; orchestrator waits until all results are in.

```bash
pip install langgraph
pip install backchannel-sdk
python graph_demo.py
```

```
▸ orchestrator fanning out 6 tasks on 'lg-work-abc123'
▸ 3 workers drain the queue (would be 3 separate processes)
▸ orchestrator awaits results on broadcast channel
  · worker-1 processed: analyze section-0
  · worker-1 processed: analyze section-1
  · worker-2 processed: analyze section-2
  · worker-2 processed: analyze section-3
  · worker-3 processed: analyze section-4
  · worker-3 processed: analyze section-5

✓ collected 6/6 results
```

For clarity the demo runs both halves in one process. In production the
worker loop runs in a separate container — the orchestrator never knows
where the workers live, only that the work eventually shows up on the
results channel.

This is the **fan-out + result-channel** pattern. Phase B2 of the
roadmap promotes it to a first-class `await_result(task_id)` API.
