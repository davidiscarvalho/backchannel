/**
 * AutoGen function definitions for Backchannel.
 * Returns plain function objects compatible with AutoGen's function_map pattern.
 */
import { BackchannelClient } from "../client.js";

export interface AutoGenFunction {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  callable: (...args: unknown[]) => Promise<unknown>;
}

export function makeBackchannelFunctions(client: BackchannelClient): AutoGenFunction[] {
  return [
    {
      name: "backchannel_send_message",
      description: "Send a coordination message to a Backchannel channel.",
      parameters: {
        type: "object",
        properties: {
          channel_id: { type: "string" },
          content: { type: "string" },
          actor_label: { type: "string" },
        },
        required: ["channel_id", "content"],
      },
      callable: async (channel_id: unknown, content: unknown, actor_label?: unknown) =>
        client.sendMessage(String(channel_id), String(content), { actorLabel: actor_label ? String(actor_label) : undefined }),
    },
    {
      name: "backchannel_list_messages",
      description: "List messages in a Backchannel channel. Pass next_cursor as since on subsequent calls.",
      parameters: {
        type: "object",
        properties: {
          channel_id: { type: "string" },
          since: { type: "string" },
        },
        required: ["channel_id"],
      },
      callable: async (channel_id: unknown, since?: unknown) =>
        client.listMessages(String(channel_id), { since: since ? String(since) : "0" }),
    },
    {
      name: "backchannel_claim_message",
      description: "Claim a task message exclusively. First caller wins.",
      parameters: {
        type: "object",
        properties: {
          message_id: { type: "string" },
          actor: { type: "string" },
        },
        required: ["message_id", "actor"],
      },
      callable: async (message_id: unknown, actor: unknown) =>
        client.claimMessage(String(message_id), { actor: String(actor) }),
    },
    {
      name: "backchannel_ack_message",
      description: "Acknowledge completion of a task message.",
      parameters: {
        type: "object",
        properties: {
          message_id: { type: "string" },
          actor: { type: "string" },
        },
        required: ["message_id", "actor"],
      },
      callable: async (message_id: unknown, actor: unknown) =>
        client.ackMessage(String(message_id), { actor: String(actor) }),
    },
  ];
}
