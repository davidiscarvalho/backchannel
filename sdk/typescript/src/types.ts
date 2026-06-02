export interface ActorRef {
  id: string;
  name: string;
}

export interface Channel {
  id: string;
  name: string;
  mode: "broadcast" | "claimable";
  access: "open" | "restricted";
  discoverable: boolean;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  channel_id: string;
  actor: ActorRef | null;
  content: string;
  actor_label: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  expires_at: string;
  /** Self-asserted claimer label. For trustworthy identity use claimed_by_key_id. */
  claimed_by: ActorRef | null;
  /** Server-verified API key holding the claim. */
  claimed_by_key_id: string | null;
  /** Member actors named on this message (those with a webhook get a push). */
  mentions: ActorRef[];
  claimed_at: string | null;
  acknowledged_by?: { id: string; name: string; occurred_at: string }[];
  active?: boolean;
}

export interface MessageEnvelope {
  message: Message;
  next_cursor: string | null;
}

export interface MessageList {
  data: Message[];
  limit: number;
  next_cursor: string | null;
}

export interface ClaimResult {
  status: "claimed" | "already_claimed";
  message: Message;
}

export interface AckResult {
  status: "acked";
  message: Message;
}

export interface Session {
  id: string;
  name: string;
  state: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  expires_at: string;
}

export interface KeyResult {
  key: string;
  key_id: string;
  expires_at: string | null;
}

export interface BackchannelErrorBody {
  error: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ClientOptions {
  apiKey: string;
  baseUrl?: string;
  timeout?: number;
}
