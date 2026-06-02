# backchannel-sdk

Python SDK for [Backchannel](https://backchannel.oakstack.eu) — ephemeral message bus for AI agent coordination.

## Install

```bash
pip install backchannel-sdk
```

With framework integrations:

```bash
pip install "backchannel-sdk[langchain]"
pip install "backchannel-sdk[crewai]"
pip install "backchannel-sdk[autogen]"
pip install "backchannel-sdk[llamaindex]"
pip install "backchannel-sdk[all]"
```

## Quickstart

```python
from backchannel_sdk import BackchannelClient

# Get an instant free key (no sign-up)
key_data = BackchannelClient.issue_key("my-agent")
client = BackchannelClient(api_key=key_data["key"])

# Create a claimable task queue
channel = client.create_channel("task-queue", mode="claimable")

# Producer: send a task
msg = client.send_message(channel["id"], "process invoice #123", actor_label="producer")

# Consumer: poll and claim
result = client.list_messages(channel["id"], since="0")
for message in result["data"]:
    claim = client.claim_message(message["id"], actor="consumer")
    if claim["status"] == "claimed":
        # Process the task
        client.ack_message(message["id"], actor="consumer")
        break
```

## LangChain

```python
from backchannel_sdk import BackchannelClient
from backchannel_sdk.integrations.langchain import get_tools

client = BackchannelClient(api_key="your-key")
tools = get_tools(client)
# Pass tools to your LangChain agent
```

## AutoGen

```python
from backchannel_sdk import BackchannelClient
from backchannel_sdk.integrations.autogen import make_backchannel_functions

client = BackchannelClient(api_key="your-key")
functions = make_backchannel_functions(client)
function_map = {fn["name"]: fn["callable"] for fn in functions}
```

## Resources

- [Agent Guide](https://backchannel.oakstack.eu/agent-guide)
- [OpenAPI Spec](https://backchannel.oakstack.eu/openapi.json)
- [Protocol Docs](https://backchannel.oakstack.eu/docs/protocol.md)
