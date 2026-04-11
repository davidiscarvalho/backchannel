/**
 * LangChain tool wrappers for Backchannel.
 * Requires @langchain/core as a peer dependency.
 */
import { BackchannelClient } from "../client.js";

// Dynamic import to keep @langchain/core optional
async function getLangChainTool() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { DynamicStructuredTool } = await import("@langchain/core/tools" as any);
  return DynamicStructuredTool;
}

export async function getTools(client: BackchannelClient) {
  const DynamicStructuredTool = await getLangChainTool();

  const sendTool = new DynamicStructuredTool({
    name: "backchannel_send",
    description:
      "Send a message to a Backchannel channel for agent coordination. Returns the message object with its id.",
    schema: {
      type: "object",
      properties: {
        channel_id: { type: "string", description: "Channel ID or alias" },
        content: { type: "string", description: "Message content" },
        actor_label: { type: "string", description: "Label identifying this agent" },
      },
      required: ["channel_id", "content"],
    },
    func: async ({ channel_id, content, actor_label }: { channel_id: string; content: string; actor_label?: string }) => {
      const msg = await client.sendMessage(channel_id, content, { actorLabel: actor_label ?? "langchain-agent" });
      return JSON.stringify(msg);
    },
  });

  const claimTool = new DynamicStructuredTool({
    name: "backchannel_claim",
    description:
      "Claim exclusive ownership of a message in a Backchannel claimable channel. First caller wins.",
    schema: {
      type: "object",
      properties: {
        message_id: { type: "string" },
        actor: { type: "string" },
      },
      required: ["message_id", "actor"],
    },
    func: async ({ message_id, actor }: { message_id: string; actor: string }) => {
      const result = await client.claimMessage(message_id, { actor });
      return JSON.stringify(result);
    },
  });

  return [sendTool, claimTool];
}
