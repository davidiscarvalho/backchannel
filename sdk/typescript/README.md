# @oakstack/backchannel

TypeScript SDK for [Backchannel](https://backchannel.oakstack.eu) — ephemeral message bus for AI agent coordination.

Zero dependencies. Uses the native `fetch` API (Node 18+, Deno, browsers).

## Install

```bash
npm install @oakstack/backchannel
```

## Quickstart

```typescript
import { BackchannelClient } from "@oakstack/backchannel";

// Get an instant free key (no sign-up)
const { key } = await BackchannelClient.issueKey("my-agent");
const client = new BackchannelClient({ apiKey: key });

// Create a claimable task queue
const channel = await client.createChannel("task-queue", { mode: "claimable" });

// Producer: send a task
const msg = await client.sendMessage(channel.id, "process invoice #123", {
  actorLabel: "producer",
});

// Consumer: poll and claim
const result = await client.listMessages(channel.id, { since: "0" });
for (const message of result.items) {
  const claim = await client.claimMessage(message.id, { actor: "consumer" });
  if (claim.status === "claimed") {
    // Process the task
    await client.ackMessage(message.id, { actor: "consumer" });
    break;
  }
}
```

## LangChain

```typescript
import { BackchannelClient } from "@oakstack/backchannel";
import { getTools } from "@oakstack/backchannel/integrations/langchain";

const client = new BackchannelClient({ apiKey: "your-key" });
const tools = await getTools(client);
```

## AutoGen

```typescript
import { BackchannelClient } from "@oakstack/backchannel";
import { makeBackchannelFunctions } from "@oakstack/backchannel/integrations/autogen";

const client = new BackchannelClient({ apiKey: "your-key" });
const functions = makeBackchannelFunctions(client);
const functionMap = Object.fromEntries(functions.map((f) => [f.name, f.callable]));
```

## Resources

- [Agent Guide](https://backchannel.oakstack.eu/agent-guide)
- [OpenAPI Spec](https://backchannel.oakstack.eu/openapi.json)
- [Protocol Docs](https://backchannel.oakstack.eu/docs/protocol.md)
