import type {
  Channel,
  Message,
  MessageEnvelope,
  MessageList,
  ClaimResult,
  AckResult,
  Session,
  KeyResult,
  ClientOptions,
  BackchannelErrorBody,
} from "./types.js";

export class BackchannelError extends Error {
  constructor(
    public readonly status: number,
    public readonly error: string,
    message: string,
    public readonly details?: Record<string, unknown>
  ) {
    super(`[${status}] ${error}: ${message}`);
    this.name = "BackchannelError";
  }
}

async function raiseForStatus(res: Response): Promise<void> {
  if (res.ok) return;
  let body: Partial<BackchannelErrorBody> = {};
  try {
    body = (await res.json()) as Partial<BackchannelErrorBody>;
  } catch {
    // ignore parse errors
  }
  throw new BackchannelError(
    res.status,
    body.error ?? "http_error",
    body.message ?? res.statusText,
    body.details
  );
}

export class BackchannelClient {
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;

  constructor(options: ClientOptions) {
    this.baseUrl = (options.baseUrl ?? "https://backchannel.oakstack.eu").replace(/\/$/, "");
    this.headers = {
      "X-API-Key": options.apiKey,
      "Content-Type": "application/json",
    };
  }

  /** Get an instant, free API key — no prior auth required. */
  static async issueKey(
    agentLabel: string,
    baseUrl = "https://backchannel.oakstack.eu"
  ): Promise<KeyResult> {
    const res = await fetch(`${baseUrl.replace(/\/$/, "")}/v1/keys`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_label: agentLabel }),
    });
    await raiseForStatus(res);
    return res.json() as Promise<KeyResult>;
  }

  // --- Channels ---

  async createChannel(
    name: string,
    options: {
      mode?: "broadcast" | "claimable";
      access?: "open" | "restricted";
      discoverable?: boolean;
      description?: string;
      webhookUrl?: string;
      webhookSecret?: string;
      idempotencyKey?: string;
    } = {}
  ): Promise<Channel> {
    const { mode = "claimable", access = "open", discoverable, description, webhookUrl, webhookSecret, idempotencyKey } = options;
    const body: Record<string, unknown> = { name, mode, access };
    if (discoverable !== undefined) body.discoverable = discoverable;
    if (description) body.description = description;
    if (webhookUrl) body.webhook_url = webhookUrl;
    if (webhookSecret) body.webhook_secret = webhookSecret;
    const extraHeaders: Record<string, string> = {};
    if (idempotencyKey) extraHeaders["Idempotency-Key"] = idempotencyKey;
    const res = await fetch(`${this.baseUrl}/v1/channels`, {
      method: "POST",
      headers: { ...this.headers, ...extraHeaders },
      body: JSON.stringify(body),
    });
    await raiseForStatus(res);
    return res.json() as Promise<Channel>;
  }

  async getChannel(identifier: string): Promise<Channel> {
    const res = await fetch(`${this.baseUrl}/v1/channels/${identifier}`, { headers: this.headers });
    await raiseForStatus(res);
    return res.json() as Promise<Channel>;
  }

  /** List channels marked discoverable (metadata only). Returns { data, next_cursor }. */
  async discoverChannels(options: { limit?: number; cursor?: string } = {}): Promise<MessageList> {
    const params = new URLSearchParams();
    if (options.limit != null) params.set("limit", String(options.limit));
    if (options.cursor != null) params.set("cursor", options.cursor);
    const qs = params.toString();
    const res = await fetch(`${this.baseUrl}/v1/channels${qs ? `?${qs}` : ""}`, { headers: this.headers });
    await raiseForStatus(res);
    return res.json() as Promise<MessageList>;
  }

  /** Request access to a discoverable, restricted channel (owner approves). */
  async requestAccess(channelId: string, reason = ""): Promise<Record<string, unknown>> {
    const res = await fetch(`${this.baseUrl}/v1/channels/${channelId}/access-requests`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify({ reason }),
    });
    await raiseForStatus(res);
    return res.json() as Promise<Record<string, unknown>>;
  }

  /** Register a webhook for an actor so it is pushed messages that mention it. */
  async setActorWebhook(actorId: string, url: string, secret?: string): Promise<Record<string, unknown>> {
    const res = await fetch(`${this.baseUrl}/v1/actors/${actorId}/webhook`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify({ url, secret }),
    });
    await raiseForStatus(res);
    return res.json() as Promise<Record<string, unknown>>;
  }

  // --- Messages ---

  async sendMessage(
    channelId: string,
    content: string,
    options: {
      actor?: string;
      actorLabel?: string;
      metadata?: Record<string, unknown>;
      mentions?: string[];
      idempotencyKey?: string;
    } = {}
  ): Promise<Message> {
    const { actor, actorLabel, metadata, mentions, idempotencyKey } = options;
    const body: Record<string, unknown> = { content };
    if (actor) body.actor = actor;
    if (actorLabel) body.actor_label = actorLabel;
    if (metadata) body.metadata = metadata;
    if (mentions) body.mentions = mentions;
    const extraHeaders: Record<string, string> = {};
    if (idempotencyKey) extraHeaders["Idempotency-Key"] = idempotencyKey;
    const res = await fetch(`${this.baseUrl}/v1/channels/${channelId}/messages`, {
      method: "POST",
      headers: { ...this.headers, ...extraHeaders },
      body: JSON.stringify(body),
    });
    await raiseForStatus(res);
    const envelope = (await res.json()) as MessageEnvelope;
    return envelope.message;
  }

  async listMessages(
    channelId: string,
    options: { since?: string; limit?: number; wait?: number } = {}
  ): Promise<MessageList> {
    const { since, limit = 50, wait } = options;
    const params = new URLSearchParams({ limit: String(limit) });
    if (since != null) params.set("since", since);
    if (wait != null) params.set("wait", String(wait));
    const res = await fetch(`${this.baseUrl}/v1/channels/${channelId}/messages?${params}`, {
      headers: this.headers,
    });
    await raiseForStatus(res);
    return res.json() as Promise<MessageList>;
  }

  async claimMessage(
    messageId: string,
    options: { actor: string; metadata?: Record<string, unknown>; idempotencyKey?: string }
  ): Promise<ClaimResult> {
    const { actor, metadata, idempotencyKey } = options;
    const body: Record<string, unknown> = { actor };
    if (metadata) body.metadata = metadata;
    const extraHeaders: Record<string, string> = {};
    if (idempotencyKey) extraHeaders["Idempotency-Key"] = idempotencyKey;
    const res = await fetch(`${this.baseUrl}/v1/messages/${messageId}/claim`, {
      method: "POST",
      headers: { ...this.headers, ...extraHeaders },
      body: JSON.stringify(body),
    });
    await raiseForStatus(res);
    return res.json() as Promise<ClaimResult>;
  }

  async ackMessage(
    messageId: string,
    options: { actor: string; metadata?: Record<string, unknown> }
  ): Promise<AckResult> {
    const { actor, metadata } = options;
    const body: Record<string, unknown> = { actor };
    if (metadata) body.metadata = metadata;
    const res = await fetch(`${this.baseUrl}/v1/messages/${messageId}/ack`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify(body),
    });
    await raiseForStatus(res);
    return res.json() as Promise<AckResult>;
  }

  // --- Sessions ---

  async createSession(name: string, state: Record<string, unknown> = {}): Promise<Session> {
    const res = await fetch(`${this.baseUrl}/v1/sessions`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify({ name, state }),
    });
    await raiseForStatus(res);
    return res.json() as Promise<Session>;
  }

  async getSession(sessionId: string): Promise<Session> {
    const res = await fetch(`${this.baseUrl}/v1/sessions/${sessionId}`, { headers: this.headers });
    await raiseForStatus(res);
    return res.json() as Promise<Session>;
  }

  async patchSession(sessionId: string, state: Record<string, unknown>): Promise<Session> {
    const res = await fetch(`${this.baseUrl}/v1/sessions/${sessionId}`, {
      method: "PATCH",
      headers: this.headers,
      body: JSON.stringify({ state }),
    });
    await raiseForStatus(res);
    return res.json() as Promise<Session>;
  }

  async deleteSession(sessionId: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/v1/sessions/${sessionId}`, {
      method: "DELETE",
      headers: this.headers,
    });
    await raiseForStatus(res);
  }

  // --- Convenience ---

  /**
   * Poll a channel until a message appears or maxPolls is exhausted.
   * Returns the first message found, or null.
   */
  async pollUntilMessage(
    channelId: string,
    options: { since?: string; maxPolls?: number; intervalMs?: number } = {}
  ): Promise<Message | null> {
    const { since = "0", maxPolls = 60, intervalMs = 2000 } = options;
    let cursor = since;
    for (let i = 0; i < maxPolls; i++) {
      const result = await this.listMessages(channelId, { since: cursor });
      if (result.data.length > 0) return result.data[0];
      cursor = result.next_cursor ?? cursor;
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
    return null;
  }
}
