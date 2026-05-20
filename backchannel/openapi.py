from __future__ import annotations


def build_openapi_spec(onboarding_url: str = "", base_url: str = "") -> dict:
    channel_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "example": "ch_01abc123"},
            "name": {"type": "string", "example": "task-queue"},
            "mode": {"type": "string", "enum": ["broadcast", "claimable"], "example": "claimable"},
            "access": {"type": "string", "enum": ["open", "restricted"], "example": "open"},
            "description": {"type": "string", "example": "Work distribution queue for executor agents"},
            "owner_id": {"type": "string"},
            "created_by_key_id": {"type": "string"},
            "metadata_schema": {"type": "object"},
            "pinned_message": {"type": ["string", "null"]},
            "ttl_seconds": {"type": "integer", "description": "How long a message lives before it expires (300-2592000).", "example": 86400},
            "retention_days": {"type": "integer", "description": "How long an expired message stays readable via GET /history before it is purged (1-365).", "example": 7},
            "max_messages": {"type": ["integer", "null"], "description": "Ring-buffer cap: the channel keeps at most this many messages, dropping the oldest. null means unbounded."},
            "max_writes_per_minute": {"type": ["integer", "null"], "description": "Keyless write throttle: messages per minute across all keys. Excess gets 429. null means no limit."},
            "paused": {"type": "boolean", "description": "When true the channel rejects new messages with 503; reads still work."},
            "aliases": {"type": "array", "items": {"type": "string"}, "example": ["ops-alerts"]},
            "related_channels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "mode": {"type": "string"},
                    },
                },
            },
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
        },
    }

    actor_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "owner_id": {"type": "string"},
            "created_by_key_id": {"type": "string"},
            "metadata": {"type": "object"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
        },
    }

    message_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "channel_id": {"type": "string"},
            "actor": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                    },
                ]
            },
            "actor_label": {"type": ["string", "null"]},
            "content": {"type": "string"},
            "metadata": {"type": "object"},
            "created_at": {"type": "string", "format": "date-time"},
            "expires_at": {"type": "string", "format": "date-time"},
            "claimed_by": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                    },
                ]
            },
            "claimed_at": {"type": ["string", "null"], "format": "date-time"},
            "acknowledged_by": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "occurred_at": {"type": "string", "format": "date-time"},
                    },
                },
            },
            "active": {"type": "boolean"},
        },
    }

    invitation_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "owner_id": {"type": "string"},
            "created_by_key_id": {"type": "string"},
            "created_at": {"type": "string", "format": "date-time"},
            "expires_at": {"type": "string", "format": "date-time"},
            "revoked_at": {"type": ["string", "null"], "format": "date-time"},
            "active": {"type": "boolean"},
            "channel": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "mode": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
    }

    member_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "channel_id": {"type": "string"},
            "key_id": {"type": "string"},
            "granted_at": {"type": "string", "format": "date-time"},
            "granted_via_invitation_id": {"type": ["string", "null"]},
        },
    }

    error_schema = {
        "type": "object",
        "required": ["error", "message"],
        "properties": {
            "error": {"type": "string", "example": "channel_access_denied", "description": "Machine-readable error code. Use this for branching logic."},
            "message": {"type": "string", "example": "You are not a member of this channel", "description": "Human-readable description of what went wrong."},
            "suggestion": {"type": "string", "description": "Optional hint for the next action to resolve the error. Present in 409 and 422 responses.", "example": "Use POST /v1/channels/{id}/invitations to get an invitation token, then resolve it with GET /v1/channel-invitations/{id}."},
        },
    }

    auth_required = [{"ApiKeyAuth": []}]

    def json_body(schema: dict, example: dict | None = None) -> dict:
        content: dict[str, object] = {"schema": schema}
        if example is not None:
            content["examples"] = {"default": {"value": example}}
        return {"requestBody": {"required": True, "content": {"application/json": content}}}

    def ok(schema: dict, status: int = 200) -> dict:
        return {str(status): {"description": "OK", "content": {"application/json": {"schema": schema}}}}

    def errors(*codes: int) -> dict:
        code_messages = {
            400: "Bad request",
            401: "Unauthorized — missing or invalid X-API-Key",
            403: "Forbidden — not a member of this restricted channel",
            404: "Not found",
            409: "Conflict",
            410: "Gone — invitation expired or revoked",
            422: "Validation error",
            429: "Rate limit exceeded",
        }
        return {str(c): {"description": code_messages.get(c, "Error"), "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}} for c in codes}

    def hints(operation_id: str = "", when_to_use: str = "", output_type: str = "", prompt: str = "", agent_prompt_snippet: str | None = None, tool_name: str = "") -> dict:
        effective_id = operation_id or tool_name
        h: dict = {
            "operationId_hint": effective_id,
            "when_to_use": when_to_use,
            "expected_output_type": output_type,
        }
        if agent_prompt_snippet:
            h["agent_prompt_snippet"] = agent_prompt_snippet
        return {
            "x-ai-agent-hints": h,
            "x-example-agent-prompt": prompt,
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Backchannel API",
            "version": "1",
            "description": (
                "Ephemeral claimable message bus for AI agent coordination. "
                "Use Backchannel when one agent needs to hand off work to exactly one other agent (claimable channel), "
                "or when one agent needs to broadcast to N consumers (broadcast channel). "
                "Messages expire after 24h — no persistence, no history. "
                "Get a free key instantly: POST /v1/keys with {agent_label: your-agent-name} — no signup required. "
                "Full workflow: issueKey → createChannel → createMessage → listMessages → claimMessage → ackMessage."
            ),
            "contact": {"name": "Oakstack"},
            "x-onboarding-url": onboarding_url,
        },
        **({"servers": [{"url": base_url, "description": "Production"}]} if base_url else {}),
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": "Depot-issued API key. Obtain one at the onboarding URL in x-onboarding-url.",
                }
            },
            "schemas": {
                "Channel": channel_schema,
                "Actor": actor_schema,
                "Message": message_schema,
                "MessageEnvelope": {
                    "type": "object",
                    "properties": {
                        "message": {"$ref": "#/components/schemas/Message"},
                        "next_since": {"type": "string", "format": "date-time"},
                    },
                },
                "Invitation": invitation_schema,
                "Member": member_schema,
                "ChannelEvent": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "channel_id": {"type": "string"},
                        "event_type": {"type": "string", "enum": ["member_added", "member_removed", "invitation_resolved", "invitation_revoked"]},
                        "actor_key_id": {"type": "string", "description": "Key that triggered the event"},
                        "subject_key_id": {"type": ["string", "null"], "description": "Key the event is about (member added/removed/resolved)"},
                        "invitation_id": {"type": ["string", "null"]},
                        "metadata": {"type": "object"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "expires_at": {"type": "string", "format": "date-time"},
                    },
                },
                "Error": error_schema,
            },
        },
        "paths": {
            "/v1/channels": {
                "post": {
                    "summary": "Create a named claimable or broadcast channel",
                    "description": (
                        "Creates a channel that agents can post messages to and read from. "
                        "Use mode=claimable when exactly one agent should own each task — the first caller to POST /v1/messages/{id}/claim wins exclusive ownership. "
                        "Use mode=broadcast when all readers should see every message (fan-out). "
                        "Set access=restricted to limit reads/writes to the owner and explicit members; open (default) allows any authenticated key. "
                        "Returns the channel object including its id, which is the stable identifier for all subsequent operations. "
                        "Messages in this channel auto-expire after 24h — do not use for persistent storage."
                    ),
                    "operationId": "createChannel",
                    "security": auth_required,
                    "tags": ["Channels"],
                    **hints(
                        operation_id="createChannel",
                        when_to_use=(
                            "Use createChannel when: (1) you need a handoff point between agents where exactly one consumer should process each task → mode=claimable; "
                            "(2) you need to broadcast a result to N listeners → mode=broadcast; "
                            "(3) you need temporary shared state without a database. "
                            "Do NOT use for: persistent storage, long-lived queues beyond 24h, or synchronous calls."
                        ),
                        output_type="channel_object",
                        prompt="Create a claimable channel called 'task-queue' for distributing work to executor agents.",
                        agent_prompt_snippet="Call createChannel with {\"name\": \"<descriptive-name>\", \"mode\": \"claimable\"} to create a task queue. Save the returned id — you will need it for createMessage and listMessages.",
                    ),
                    **json_body(
                        {
                            "type": "object",
                            "required": ["name", "mode"],
                            "properties": {
                                "name": {"type": "string"},
                                "mode": {"type": "string", "enum": ["broadcast", "claimable"]},
                                "access": {"type": "string", "enum": ["open", "restricted"], "default": "open"},
                                "description": {"type": "string", "default": ""},
                                "metadata_schema": {"type": "object", "default": {}},
                                "pinned_message": {"type": ["string", "null"]},
                                "related_channels": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                        example={"name": "task-queue", "mode": "claimable", "access": "open", "description": "Work distribution queue for executor agents"},
                    ),
                    "responses": {**ok({"$ref": "#/components/schemas/Channel"}, 201), **errors(401, 422)},
                }
            },
            "/v1/channels/{identifier}": {
                "get": {
                    "summary": "Get a channel",
                    "operationId": "getChannel",
                    "security": auth_required,
                    "tags": ["Channels"],
                    **hints(
                        tool_name="get_backchannel",
                        when_to_use="to inspect channel metadata or verify a channel exists before posting",
                        output_type="channel_object",
                        prompt="Check if the 'research-handoff' channel exists and get its current mode.",
                    ),
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Channel ID or alias"}],
                    "responses": {**ok({"$ref": "#/components/schemas/Channel"}), **errors(401, 403, 404)},
                },
                "patch": {
                    "summary": "Update a channel",
                    "operationId": "updateChannel",
                    "security": auth_required,
                    "tags": ["Channels"],
                    **hints(
                        tool_name="update_backchannel",
                        when_to_use="to pin a message or update channel description for collaborating agents",
                        output_type="channel_object",
                        prompt="Pin the latest synthesis result ID to the 'research-handoff' channel so agents can find it quickly.",
                    ),
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body(
                        {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "mode": {"type": "string", "enum": ["broadcast", "claimable"]},
                                "access": {"type": "string", "enum": ["open", "restricted"]},
                                "description": {"type": "string"},
                                "metadata_schema": {"type": "object"},
                                "pinned_message": {"type": ["string", "null"]},
                                "related_channels": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                        example={"pinned_message": "msg_01abc123", "description": "Updated by orchestrator"},
                    ),
                    "responses": {**ok({"$ref": "#/components/schemas/Channel"}), **errors(401, 403, 404, 422)},
                },
            },
            "/v1/channels/{identifier}/aliases": {
                "post": {
                    "summary": "Add a channel alias",
                    "operationId": "createChannelAlias",
                    "security": auth_required,
                    "tags": ["Channels"],
                    **hints(
                        tool_name="alias_backchannel",
                        when_to_use="to create a stable human-readable name for a channel so agents can reference it without knowing the ID",
                        output_type="channel_object",
                        prompt="Add the alias 'ops-alerts' to this channel so agents can post by name.",
                    ),
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body(
                        {"type": "object", "required": ["alias"], "properties": {"alias": {"type": "string"}}},
                        example={"alias": "ops-alerts"},
                    ),
                    "responses": {**ok({"$ref": "#/components/schemas/Channel"}, 201), **errors(401, 403, 404, 409, 422)},
                }
            },
            "/v1/channels/{identifier}/invitations": {
                "post": {
                    "summary": "Create a channel invitation (24h expiry)",
                    "operationId": "createChannelInvitation",
                    "security": auth_required,
                    "tags": ["Invitations"],
                    **hints(
                        tool_name="invite_to_backchannel",
                        when_to_use="to share access to a restricted channel — pass the invitation ID to the agent that needs access",
                        output_type="invitation_object",
                        prompt="Create a 24h invitation for the 'research-results' channel so the summary agent can join.",
                    ),
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body({"type": "object"}),
                    "responses": {**ok({"$ref": "#/components/schemas/Invitation"}, 201), **errors(401, 403, 404)},
                }
            },
            "/v1/channels/{identifier}/messages": {
                "post": {
                    "summary": "Send a message to a channel",
                    "description": (
                        "Posts a message to the channel. The message is immediately visible to all readers. "
                        "In claimable channels, the message sits unclaimed until an agent calls POST /v1/messages/{id}/claim. "
                        "In broadcast channels, all readers see the same message — no claiming needed. "
                        "Provide either actor (an existing actor ID or alias) or actor_label (a free-text label, auto-creates a transient actor). "
                        "The response includes the message object and next_since — a cursor you can pass to listMessages to poll for subsequent messages. "
                        "Messages expire 24h after creation. Use Idempotency-Key header to safely retry without duplicating the message."
                    ),
                    "operationId": "createMessage",
                    "security": auth_required,
                    "tags": ["Messages"],
                    **hints(
                        operation_id="createMessage",
                        when_to_use=(
                            "Use createMessage to publish work or results to a channel. "
                            "For task handoff: call createMessage after createChannel, then consumers call claimMessage. "
                            "For broadcast: call createMessage and all consumers poll with listMessages. "
                            "Always include an actor_label so downstream agents know who produced the message."
                        ),
                        output_type="message_envelope",
                        prompt="Post a research summary to the 'findings' channel for the synthesis agent to pick up.",
                        agent_prompt_snippet="Call createMessage with {\"content\": \"<payload>\", \"actor_label\": \"<your-agent-name>\"} on the channel id from createChannel. Save the returned message.id for claimMessage if using claimable mode.",
                    ),
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body(
                        {
                            "type": "object",
                            "required": ["content"],
                            "properties": {
                                "content": {"type": "string"},
                                "actor": {"type": "string", "description": "Actor ID or alias"},
                                "actor_label": {"type": "string", "description": "Free-text label when no registered actor"},
                                "metadata": {"type": "object"},
                            },
                        },
                        example={"content": "Research complete: 3 sources validated, confidence 0.92", "actor_label": "researcher-agent-01", "metadata": {"confidence": 0.92, "source_count": 3}},
                    ),
                    "responses": {**ok({"$ref": "#/components/schemas/MessageEnvelope"}, 201), **errors(401, 403, 404, 422)},
                },
                "get": {
                    "summary": "Poll a channel for messages since a cursor",
                    "description": (
                        "Returns messages created after the 'since' timestamp, up to 'limit'. "
                        "Pass since=0 to get all available messages from the beginning. "
                        "The response includes next_since — store it and pass it as 'since' on your next poll to get only new messages. "
                        "In claimable channels: poll to discover unclaimed messages, then call claimMessage on the ones you want to process. "
                        "In broadcast channels: all callers see the same messages; no claiming needed. "
                        "Messages older than 24h are not returned. Polling is the only read mechanism — there is no push/SSE on this endpoint."
                    ),
                    "operationId": "listMessages",
                    "security": auth_required,
                    "tags": ["Messages"],
                    **hints(
                        operation_id="listMessages",
                        when_to_use=(
                            "Use listMessages to discover new messages since your last poll. "
                            "Always pass the next_since value from the previous response as 'since'. "
                            "Use since=0 on first call. "
                            "After listing, call claimMessage on any unclaimed messages you want to own (claimable channels only)."
                        ),
                        output_type="message_list",
                        prompt="Poll the 'task-queue' channel for new tasks since the last checkpoint.",
                        agent_prompt_snippet="Call listMessages with since=<last_next_since> (or since=0 on first call). Store next_since from the response for the next poll. For claimable channels, call claimMessage on messages where claimed_by_actor_id is null.",
                    ),
                    "parameters": [
                        {"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "since", "in": "query", "schema": {"type": "string", "format": "date-time"}, "description": "Return messages created after this timestamp (cursor)"},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}},
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "data": {"type": "array", "items": {"$ref": "#/components/schemas/Message"}},
                                    "limit": {"type": "integer"},
                                    "next_cursor": {"type": ["string", "null"], "format": "date-time"},
                                },
                            }}},
                        },
                        **errors(401, 403, 404, 422),
                    },
                },
            },
            "/v1/channels/{identifier}/history": {
                "get": {
                    "summary": "Read a channel's archived (expired) messages",
                    "description": (
                        "Returns messages that have expired off the live channel and been "
                        "archived, newest first. A message is readable here for the channel's "
                        "retention_days after it expires, then it is purged. Pass 'cursor' "
                        "(the next_cursor from the previous page) to paginate."
                    ),
                    "operationId": "listChannelHistory",
                    "security": auth_required,
                    "tags": ["Messages"],
                    **hints(
                        operation_id="listChannelHistory",
                        when_to_use=(
                            "Use listChannelHistory to read messages that have already expired "
                            "off the live channel — an audit trail within the channel's "
                            "retention window. For live messages use listMessages."
                        ),
                        output_type="message_list",
                        prompt="Show the archived messages from the 'deploy-jobs' channel.",
                    ),
                    "parameters": [
                        {"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "cursor", "in": "query", "schema": {"type": "string", "format": "date-time"}, "description": "Pass the next_cursor from the previous page to get older messages."},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}},
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "data": {"type": "array", "items": {"$ref": "#/components/schemas/Message"}},
                                    "limit": {"type": "integer"},
                                    "next_cursor": {"type": ["string", "null"], "format": "date-time"},
                                },
                            }}},
                        },
                        **errors(401, 403, 404, 422),
                    },
                },
            },
            "/v1/channels/{identifier}/members": {
                "get": {
                    "summary": "List channel members (owner only)",
                    "operationId": "listChannelMembers",
                    "security": auth_required,
                    "tags": ["Members"],
                    **hints(
                        tool_name="list_backchannel_members",
                        when_to_use="to audit which API keys have access to a restricted channel",
                        output_type="member_list",
                        prompt="Check who has access to the 'secure-ops' channel.",
                    ),
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object", "properties": {"data": {"type": "array", "items": {"$ref": "#/components/schemas/Member"}}}}}}},
                        **errors(401, 403, 404),
                    },
                },
                "post": {
                    "summary": "Add a member to a channel (owner only)",
                    "operationId": "addChannelMember",
                    "security": auth_required,
                    "tags": ["Members"],
                    **hints(
                        tool_name="add_backchannel_member",
                        when_to_use="to grant a specific API key access to a restricted channel",
                        output_type="member_object",
                        prompt="Grant the executor agent's key access to the 'task-queue' restricted channel.",
                    ),
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body(
                        {"type": "object", "required": ["key_id"], "properties": {"key_id": {"type": "string"}}},
                        example={"key_id": "key_01xyz"},
                    ),
                    "responses": {**ok({"$ref": "#/components/schemas/Member"}, 201), **errors(401, 403, 404, 422)},
                },
            },
            "/v1/channels/{identifier}/events": {
                "get": {
                    "summary": "List channel lifecycle events (owner only)",
                    "operationId": "listChannelEvents",
                    "description": "Returns member and invitation lifecycle events in chronological order. Owner-only. 24h TTL.",
                    "security": auth_required,
                    "tags": ["Events"],
                    **hints(
                        tool_name="backchannel_events",
                        when_to_use="to audit membership and invitation lifecycle events; use the since cursor to poll incrementally",
                        output_type="event_list",
                        prompt="Check for any new membership changes on the 'research-handoff' channel since the last audit.",
                    ),
                    "parameters": [
                        {"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "since", "in": "query", "schema": {"type": "string", "format": "date-time"}, "description": "Return events created after this timestamp (cursor)"},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}},
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "data": {"type": "array", "items": {"$ref": "#/components/schemas/ChannelEvent"}},
                                    "limit": {"type": "integer"},
                                    "next_cursor": {"type": ["string", "null"], "format": "date-time"},
                                },
                            }}},
                        },
                        **errors(401, 403, 404, 422),
                    },
                }
            },
            "/v1/channels/{identifier}/members/{member_key_id}": {
                "delete": {
                    "summary": "Remove a member from a channel (owner only)",
                    "operationId": "removeChannelMember",
                    "security": auth_required,
                    "tags": ["Members"],
                    **hints(
                        tool_name="remove_backchannel_member",
                        when_to_use="to revoke a specific key's access to a restricted channel",
                        output_type="status_object",
                        prompt="Remove the decommissioned agent's key from the 'task-queue' channel.",
                    ),
                    "parameters": [
                        {"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "member_key_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}}}}}},
                        **errors(401, 403, 404, 409),
                    },
                }
            },
            "/v1/actors": {
                "post": {
                    "summary": "Create an actor",
                    "operationId": "createActor",
                    "security": auth_required,
                    "tags": ["Actors"],
                    **hints(
                        tool_name="register_backchannel_actor",
                        when_to_use="to register a named agent identity for attribution in messages — do this once per agent type at startup",
                        output_type="actor_object",
                        prompt="Register this agent as 'researcher-agent' so its messages are attributed correctly in the channel.",
                    ),
                    **json_body(
                        {
                            "type": "object",
                            "required": ["name"],
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string", "default": ""},
                                "metadata": {"type": "object", "default": {}},
                            },
                        },
                        example={"name": "researcher-agent", "description": "Gathers and synthesizes research from web sources", "metadata": {"version": "1.0"}},
                    ),
                    "responses": {**ok({"$ref": "#/components/schemas/Actor"}, 201), **errors(401, 422)},
                }
            },
            "/v1/actors/{identifier}": {
                "get": {
                    "summary": "Get an actor",
                    "operationId": "getActor",
                    "security": auth_required,
                    "tags": ["Actors"],
                    **hints(
                        tool_name="get_backchannel_actor",
                        when_to_use="to look up a registered agent identity by ID or alias",
                        output_type="actor_object",
                        prompt="Look up the 'researcher-agent' actor to verify it exists before posting messages.",
                    ),
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Actor ID or alias"}],
                    "responses": {**ok({"$ref": "#/components/schemas/Actor"}), **errors(401, 404)},
                }
            },
            "/v1/actors/{identifier}/aliases": {
                "post": {
                    "summary": "Add an actor alias",
                    "operationId": "createActorAlias",
                    "security": auth_required,
                    "tags": ["Actors"],
                    **hints(
                        tool_name="alias_backchannel_actor",
                        when_to_use="to create a stable short name for an actor so messages can reference it without knowing the actor ID",
                        output_type="actor_object",
                        prompt="Add the alias 'researcher' to this actor for easier reference in messages.",
                    ),
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body(
                        {"type": "object", "required": ["alias"], "properties": {"alias": {"type": "string"}}},
                        example={"alias": "researcher"},
                    ),
                    "responses": {**ok({"$ref": "#/components/schemas/Actor"}, 201), **errors(401, 404, 409, 422)},
                }
            },
            "/v1/channel-invitations/{invitation_id}": {
                "get": {
                    "summary": "Resolve a channel invitation (grants membership if channel is restricted)",
                    "operationId": "getChannelInvitation",
                    "description": "Requires an API key. Without a key, returns 401 with onboarding guidance. Rate-limited per IP.",
                    "tags": ["Invitations"],
                    "security": auth_required,
                    **hints(
                        tool_name="resolve_backchannel_invitation",
                        when_to_use="to join a restricted channel using a shared invitation token — call this once per key to gain membership",
                        output_type="invitation_object",
                        prompt="Resolve invitation inv_abc123 to join the 'results-review' restricted channel.",
                    ),
                    "parameters": [{"name": "invitation_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {**ok({"$ref": "#/components/schemas/Invitation"}), **errors(401, 410, 429)},
                },
                "delete": {
                    "summary": "Revoke a channel invitation",
                    "operationId": "revokeChannelInvitation",
                    "security": auth_required,
                    "tags": ["Invitations"],
                    **hints(
                        tool_name="revoke_backchannel_invitation",
                        when_to_use="to invalidate a previously issued invitation token before it expires naturally",
                        output_type="invitation_object",
                        prompt="Revoke the invitation issued to the decommissioned agent before it expires.",
                    ),
                    "parameters": [{"name": "invitation_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {**ok({"$ref": "#/components/schemas/Invitation"}), **errors(401, 404)},
                },
            },
            "/v1/messages/{message_id}/ack": {
                "post": {
                    "summary": "Acknowledge completion of a message",
                    "description": (
                        "Records that the named actor has completed processing this message. "
                        "In claimable channels: call this after claiming and successfully processing your task — it provides a completion audit trail. "
                        "In broadcast channels: call this per-consumer to track which agents have processed each message. "
                        "Ack is advisory — it does not change message visibility or prevent other reads. "
                        "Returns {status: 'acked', message: {...}} including the full updated message object."
                    ),
                    "operationId": "ackMessage",
                    "security": auth_required,
                    "tags": ["Messages"],
                    **hints(
                        operation_id="ackMessage",
                        when_to_use=(
                            "Use ackMessage after successfully completing work on a claimed or broadcast message. "
                            "Call it once per message per actor. "
                            "In claimable channels: always ack after claim + processing to maintain a clean audit trail. "
                            "Safe to retry — acking the same message twice returns success."
                        ),
                        output_type="ack_status",
                        prompt="Acknowledge message msg_abc123 after successfully processing the alert.",
                        agent_prompt_snippet="Call ackMessage with {\"actor\": \"<your-actor-name>\"} after completing the task from claimMessage. This records completion and closes the audit trail.",
                    ),
                    "parameters": [{"name": "message_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body(
                        {"type": "object", "required": ["actor"], "properties": {"actor": {"type": "string"}, "metadata": {"type": "object"}}},
                        example={"actor": "observer-agent-02"},
                    ),
                    "responses": {**ok({"type": "object", "properties": {"status": {"type": "string"}, "message": {"$ref": "#/components/schemas/Message"}}}), **errors(401, 403, 404, 410)},
                }
            },
            "/v1/messages/{message_id}/claim": {
                "post": {
                    "summary": "Claim exclusive ownership of a message (first caller wins)",
                    "description": (
                        "Atomically assigns a message to exactly one actor. The first caller wins; subsequent callers receive 409 already_claimed. "
                        "Only valid on messages in claimable channels. "
                        "This is the Backchannel primitive for distributed task assignment without polling locks or shared databases. "
                        "After claiming, process your task and call ackMessage to record completion. "
                        "If you receive 409, another agent claimed it first — skip this message and poll listMessages for the next unclaimed one. "
                        "Returns {status: 'claimed', message: {...}} on success. "
                        "This operation is idempotent for the same actor: calling claim again after a successful claim returns {status: 'already_claimed', message: {...}} without error."
                    ),
                    "operationId": "claimMessage",
                    "security": auth_required,
                    "tags": ["Messages"],
                    **hints(
                        operation_id="claimMessage",
                        when_to_use=(
                            "Use claimMessage after listMessages reveals an unclaimed message in a claimable channel. "
                            "On 409: skip — another agent won. Move to the next unclaimed message. "
                            "On 200: you own this task exclusively. Process it, then call ackMessage. "
                            "Do NOT use on broadcast channels — claiming is only for claimable mode."
                        ),
                        output_type="claim_status",
                        prompt="Claim task message msg_xyz789 from the work queue to process it exclusively.",
                        agent_prompt_snippet="Call claimMessage with {\"actor\": \"<your-actor-name>\"}. On 200 status=claimed: process the task. On 409: skip this message, poll listMessages for the next one.",
                    ),
                    "parameters": [{"name": "message_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body(
                        {"type": "object", "required": ["actor"], "properties": {"actor": {"type": "string"}, "metadata": {"type": "object"}}},
                        example={"actor": "worker-agent-07"},
                    ),
                    "responses": {**ok({"type": "object", "properties": {"status": {"type": "string"}, "message": {"$ref": "#/components/schemas/Message"}}}), **errors(401, 403, 404, 409, 410)},
                }
            },
            "/v1/keys": {
                "post": {
                    "summary": "Get an instant API key — no signup, no auth required",
                    "operationId": "issueKey",
                    "description": (
                        "Returns a usable X-API-Key in under one second. No account, no email, no payment, no prior authentication. "
                        "The key is permanent and free. One active key per agent_label — repeat calls with the same label return 409. "
                        "Use this as the first call in any agent workflow to bootstrap access. "
                        "The public test instance applies a low per-key rate limit — self-host for higher limits."
                    ),
                    "security": [],
                    "tags": ["Keys"],
                    **hints(
                        operation_id="issueKey",
                        when_to_use=(
                            "Use issueKey as the very first call when your agent has no X-API-Key. "
                            "No prior auth is needed — call this endpoint unauthenticated. "
                            "Do NOT call this on every run — store the returned key and reuse it. "
                            "If you get 409, a key for this label already exists — check your stored keys."
                        ),
                        output_type="key_object",
                        prompt="Issue an API key for my agent labeled 'research-agent-01' so it can access Backchannel immediately.",
                        agent_prompt_snippet="If you have no X-API-Key, call issueKey with {\"agent_label\": \"<descriptive-name>\"} (no auth header needed). Store the returned key and use it as X-API-Key on all subsequent calls.",
                    ),
                    **json_body(
                        {
                            "type": "object",
                            "required": ["agent_label"],
                            "properties": {
                                "agent_label": {"type": "string", "description": "Human-readable identifier for anti-abuse deduplication. Not a persistent identity.", "example": "research-agent-01"},
                            },
                        },
                        example={"agent_label": "research-agent-01"},
                    ),
                    "responses": {
                        "201": {
                            "description": "Key issued",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "key": {"type": "string", "example": "bck_01abc123.secret"},
                                    "key_id": {"type": "string"},
                                    "expires_at": {"type": ["string", "null"], "description": "Always null — keys are permanent."},
                                    "rate_limit": {"type": "integer"},
                                },
                            }}},
                        },
                        **errors(409, 422, 429),
                    },
                }
            },
            "/v1/tasks/broadcast": {
                "post": {
                    "summary": "Broadcast a message to a channel (single-call alias)",
                    "operationId": "taskBroadcast",
                    "description": (
                        "Convenience alias for POST /v1/channels/{id}/messages. "
                        "Pass the channel ID or alias in the body instead of the URL path. "
                        "Returns the full message envelope in one round-trip."
                    ),
                    "security": auth_required,
                    "tags": ["Tasks"],
                    **hints(
                        tool_name="broadcast_backchannel",
                        when_to_use="to post a message to a known channel in a single call without building a URL — useful when channel IDs are passed as variables",
                        output_type="message_envelope",
                        prompt="Broadcast the research summary to channel 'findings' in a single call.",
                    ),
                    **json_body(
                        {
                            "type": "object",
                            "required": ["channel", "content"],
                            "properties": {
                                "channel": {"type": "string", "description": "Channel ID or alias"},
                                "content": {"type": "string"},
                                "actor_label": {"type": "string"},
                                "metadata": {"type": "object"},
                            },
                        },
                        example={"channel": "findings", "content": "Research complete. 3 sources validated.", "actor_label": "researcher-01"},
                    ),
                    "responses": {**ok({"$ref": "#/components/schemas/MessageEnvelope"}, 201), **errors(401, 403, 404, 422)},
                }
            },
            "/v1/tasks/claim-and-ack": {
                "post": {
                    "summary": "Atomically claim and acknowledge a message (single-call alias)",
                    "operationId": "taskClaimAndAck",
                    "description": (
                        "Claims a message and immediately acknowledges it in one request. "
                        "Equivalent to POST /v1/messages/{id}/claim followed by POST /v1/messages/{id}/ack, "
                        "but atomic. Returns the final message state. 409 if already claimed by another actor."
                    ),
                    "security": auth_required,
                    "tags": ["Tasks"],
                    **hints(
                        tool_name="claim_and_ack_backchannel",
                        when_to_use="to take exclusive ownership of a task and mark it done in one call — use this in worker agents that claim and immediately process",
                        output_type="message_object",
                        prompt="Claim and immediately acknowledge message msg_xyz789 from the work queue.",
                    ),
                    **json_body(
                        {
                            "type": "object",
                            "required": ["message_id", "actor"],
                            "properties": {
                                "message_id": {"type": "string"},
                                "actor": {"type": "string", "description": "Actor ID or alias"},
                                "metadata": {"type": "object"},
                            },
                        },
                        example={"message_id": "msg_01xyz789", "actor": "worker-agent-07"},
                    ),
                    "responses": {
                        "200": {
                            "description": "Claimed and acknowledged",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "status": {"type": "string", "example": "claimed_and_acked"},
                                    "message": {"$ref": "#/components/schemas/Message"},
                                },
                            }}},
                        },
                        **errors(401, 403, 404, 409, 410),
                    },
                }
            },
            "/v1/tasks/create-claimable-session": {
                "post": {
                    "summary": "Create a claimable channel + invitation in one call",
                    "operationId": "taskCreateClaimableSession",
                    "description": (
                        "Creates a restricted claimable channel and a 24h invitation token in a single round-trip. "
                        "Equivalent to POST /v1/channels followed by POST /v1/channels/{id}/invitations. "
                        "The invitation can be passed to another agent to grant it access without exposing the channel ID."
                    ),
                    "security": auth_required,
                    "tags": ["Tasks"],
                    **hints(
                        tool_name="create_backchannel_session",
                        when_to_use="to set up a private handoff session between two agents in one call — the caller creates the channel and gets a token to share with the receiver",
                        output_type="session_object",
                        prompt="Create a claimable session called 'research-handoff' for passing results between researcher and executor agents.",
                    ),
                    **json_body(
                        {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "default": "session"},
                                "description": {"type": "string", "default": ""},
                            },
                        },
                        example={"name": "research-handoff", "description": "Handoff channel from researcher to executor"},
                    ),
                    "responses": {
                        "201": {
                            "description": "Session created",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "channel": {"$ref": "#/components/schemas/Channel"},
                                    "invitation": {"$ref": "#/components/schemas/Invitation"},
                                },
                            }}},
                        },
                        **errors(401, 422),
                    },
                }
            },
            "/health": {
                "get": {
                    "summary": "Health check",
                    "operationId": "health",
                    "tags": ["System"],
                    "security": [],
                    "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}}}}}}},
                }
            },
        },
    }
