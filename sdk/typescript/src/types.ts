export interface Channel {
  id: string;
  name: string;
  mode: "broadcast" | "claimable";
  access: "open" | "restricted";
  description: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  channel_id: string;
  content: string;
  actor_label: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  expires_at: string;
  claimed_by_actor_id: string | null;
  claimed_at: string | null;
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
  tier: number;
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
