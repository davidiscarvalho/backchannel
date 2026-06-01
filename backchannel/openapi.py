from __future__ import annotations

# Meta routes intentionally omitted from the OpenAPI spec — these are
# discovery, docs, health, and ops surfaces that an agent does not
# call programmatically as part of the agent verb workflow:
#   GET /                                  (root HTML)
#   GET /openapi.json                      (this document)
#   GET /agent-guide                       (markdown for humans/agents)
#   GET /ai-manifest.json                  (AI plugin manifest)
#   GET /.well-known/backchannel.json
#   GET /.well-known/ai-manifest.json
#   GET /.well-known/openapi.json
#   GET /.well-known/ai-plugin.json
#   GET /.well-known/agent-policy.json
#   GET /first-success-prompt.txt
#   GET /llms.txt
#   GET /docs/{document}.md                (markdown docs)
#   GET /docs/playground                   (HTML playground)
#   GET /metrics                           (Prometheus)
#   GET /robots.txt
#   GET /status, GET /status.html          (status page)
#   GET /account/usage                     (HTML usage page)
# If you add a non-meta route to backchannel/http.py, add it here too;
# tests/test_openapi_completeness.py will fail otherwise.


# Mutating HTTP methods that should accept an Idempotency-Key header.
_MUTATING_METHODS = {"post", "patch", "put", "delete"}


def _idempotency_key_param() -> dict:
    return {
        "name": "Idempotency-Key",
        "in": "header",
        "required": False,
        "schema": {"type": "string"},
        "description": (
            "Client-supplied key to make this request safely retryable. "
            "The server stores the response for a short window and replays "
            "it on retries with the same key, so network retries do not "
            "produce duplicates."
        ),
    }


def _inject_idempotency_keys(paths: dict) -> None:
    """Add an Idempotency-Key header parameter to every mutating
    operation that does not already declare one."""
    for _path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in _MUTATING_METHODS:
                continue
            if not isinstance(op, dict):
                continue
            params = list(op.get("parameters", []))
            if any(
                isinstance(p, dict)
                and p.get("in") == "header"
                and p.get("name", "").lower() == "idempotency-key"
                for p in params
            ):
                continue
            params.append(_idempotency_key_param())
            op["parameters"] = params


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
                "description": "Self-asserted actor label of the claimer. For trustworthy identity use claimed_by_key_id.",
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                    },
                ],
            },
            "claimed_by_key_id": {
                "type": ["string", "null"],
                "description": "Server-verified API key that holds the claim — trustworthy attribution, unlike the self-asserted claimed_by label.",
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

    spec = {
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
                    "description": "Self-issued API key. Get one for free via POST /v1/keys — no sign-up.",
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
                        "next_cursor": {"type": ["string", "null"], "format": "date-time"},
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
                        "The response includes the message object and next_cursor — a cursor you can pass to listMessages (as 'since') to poll for subsequent messages. "
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
                        "The response is {\"data\": [...messages...], \"next_cursor\": \"<cursor>\"} — store next_cursor and pass it as 'since' on your next poll to get only new messages. "
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
                            "Always pass the next_cursor value from the previous response as 'since'. "
                            "Use since=0 on first call. "
                            "After listing, call claimMessage on any unclaimed messages you want to own (claimable channels only)."
                        ),
                        output_type="message_list",
                        prompt="Poll the 'task-queue' channel for new tasks since the last checkpoint.",
                        agent_prompt_snippet="Call listMessages with since=<last_next_cursor> (or since=0 on first call). Read messages from the response's 'data' array and store 'next_cursor' for the next poll. For claimable channels, call claimMessage on messages where claimed_by is null.",
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

    message_id_param = {"name": "message_id", "in": "path", "required": True, "schema": {"type": "string"}}
    identifier_param = {"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Channel ID or alias"}
    session_id_param = {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}

    extra_paths = {
        "/v1/messages/{message_id}/release": {
            "post": {
                "summary": "Release a claim on a message",
                "description": (
                    "Releases a previously-claimed message so another actor can claim it. "
                    "Use when an agent crashes mid-task or decides not to process a claimed message."
                ),
                "operationId": "releaseMessage",
                "security": auth_required,
                "tags": ["Messages"],
                **hints(
                    operation_id="releaseMessage",
                    when_to_use="to give up a claim you no longer want to fulfill so another agent can take over",
                    output_type="release_status",
                    prompt="Release the claim on message msg_xyz789 so another worker can pick it up.",
                    agent_prompt_snippet="Call releaseMessage with {\"actor\": \"<your-actor-name>\"} to undo a claim you cannot complete.",
                ),
                "parameters": [message_id_param],
                **json_body(
                    {"type": "object", "required": ["actor"], "properties": {"actor": {"type": "string"}, "metadata": {"type": "object"}}},
                    example={"actor": "worker-agent-07"},
                ),
                "responses": {**ok({"type": "object", "properties": {"status": {"type": "string"}, "message": {"$ref": "#/components/schemas/Message"}}}), **errors(401, 403, 404, 409)},
            }
        },
        "/v1/messages/{message_id}/claim-with-lease": {
            "post": {
                "summary": "Claim a message with a heartbeat lease",
                "description": (
                    "Claims a message and returns a lease_token + lease_expires_at. "
                    "The claim is auto-released if the holder fails to call POST /v1/leases/{lease_token}/heartbeat "
                    "before lease_expires_at — protects against agents that crash holding a claim."
                ),
                "operationId": "claimMessageWithLease",
                "security": auth_required,
                "tags": ["Messages"],
                **hints(
                    operation_id="claimMessageWithLease",
                    when_to_use="when processing may take longer than the channel TTL would otherwise allow, or when crash-resilience matters — call heartbeat_lease periodically to keep the claim",
                    output_type="lease_object",
                    prompt="Claim message msg_xyz789 with a 60s lease so the claim is released automatically if I crash.",
                ),
                "parameters": [message_id_param],
                **json_body(
                    {
                        "type": "object",
                        "required": ["actor"],
                        "properties": {
                            "actor": {"type": "string"},
                            "lease_seconds": {"type": "integer", "minimum": 1, "description": "Lease duration in seconds (server-bounded)."},
                            "metadata": {"type": "object"},
                        },
                    },
                    example={"actor": "worker-agent-07", "lease_seconds": 60},
                ),
                "responses": {
                    "200": {
                        "description": "Claimed with lease",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string", "example": "claimed"},
                                "message": {"$ref": "#/components/schemas/Message"},
                                "lease_token": {"type": "string"},
                                "lease_expires_at": {"type": "string", "format": "date-time"},
                            },
                        }}},
                    },
                    **errors(401, 403, 404, 409, 410),
                },
            }
        },
        "/v1/leases/{lease_token}/heartbeat": {
            "post": {
                "summary": "Extend a lease on a claimed message",
                "description": (
                    "Pushes back the lease_expires_at so the claim is not auto-released. "
                    "Call periodically (e.g. every lease_seconds / 2) while processing."
                ),
                "operationId": "heartbeatLease",
                "security": auth_required,
                "tags": ["Messages"],
                **hints(
                    operation_id="heartbeatLease",
                    when_to_use="while still working on a leased claim — call before lease_expires_at to keep the claim alive",
                    output_type="lease_object",
                    prompt="Heartbeat the lease on the long-running task so it does not get auto-released.",
                ),
                "parameters": [{"name": "lease_token", "in": "path", "required": True, "schema": {"type": "string"}}],
                **json_body(
                    {"type": "object", "properties": {"lease_seconds": {"type": "integer", "minimum": 1}}},
                    example={"lease_seconds": 60},
                ),
                "responses": {
                    "200": {
                        "description": "Lease extended",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "lease_token": {"type": "string"},
                                "lease_expires_at": {"type": "string", "format": "date-time"},
                                "message": {"$ref": "#/components/schemas/Message"},
                            },
                        }}},
                    },
                    **errors(401, 403, 404, 410),
                },
            }
        },
        "/v1/messages/{message_id}": {
            "delete": {
                "summary": "Retract a message you posted",
                "description": "The author of a message may retract it. Returns {status: 'retracted'}.",
                "operationId": "deleteMessage",
                "security": auth_required,
                "tags": ["Messages"],
                **hints(
                    operation_id="deleteMessage",
                    when_to_use="to retract a message you posted by mistake or that contains sensitive data",
                    output_type="status_object",
                    prompt="Retract message msg_xyz789 — it was posted with stale data.",
                ),
                "parameters": [message_id_param],
                "responses": {
                    "200": {"description": "Retracted", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}}}}}},
                    **errors(401, 403, 404),
                },
            }
        },
        "/v1/channels/{identifier}": {
            "delete": {
                "summary": "Delete a channel (owner only)",
                "description": "Permanently deletes a channel and its messages. Owner of the channel only. Returns 204 No Content on success.",
                "operationId": "deleteChannel",
                "security": auth_required,
                "tags": ["Channels"],
                **hints(
                    operation_id="deleteChannel",
                    when_to_use="to permanently remove a channel and all its messages when work is done — only the channel owner can call this",
                    output_type="status_object",
                    prompt="Delete the 'task-queue' channel now that the workflow is complete.",
                ),
                "parameters": [identifier_param],
                "responses": {"204": {"description": "Deleted"}, **errors(401, 403, 404)},
            }
        },
        "/v1/tasks/post": {
            "post": {
                "summary": "Hand a task to one consumer (verb-style alias)",
                "description": (
                    "Creates the claimable channel if missing and posts a task message in one call. "
                    "Verb-style alias for createChannel(mode=claimable) + createMessage."
                ),
                "operationId": "taskPost",
                "security": auth_required,
                "tags": ["Tasks"],
                **hints(
                    operation_id="taskPost",
                    when_to_use="to hand a task to exactly one consumer on a known channel name in a single call",
                    output_type="message_envelope",
                    prompt="Post a task to the 'jobs' channel for any worker to claim.",
                    agent_prompt_snippet="Call taskPost with {\"channel\": \"<channel-name>\", \"content\": \"<task>\", \"actor_label\": \"<you>\"}. The channel is created on the fly if it does not exist.",
                ),
                **json_body(
                    {
                        "type": "object",
                        "required": ["channel", "content"],
                        "properties": {
                            "channel": {"type": "string", "description": "Channel name or ID; created on the fly if missing"},
                            "content": {"type": "string"},
                            "actor_label": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                    },
                    example={"channel": "jobs", "content": "Resize image 42", "actor_label": "orchestrator"},
                ),
                "responses": {
                    "201": {"description": "Task posted", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "message": {"$ref": "#/components/schemas/Message"},
                            "channel": {"type": "string"},
                            "next_cursor": {"type": ["string", "null"], "format": "date-time"},
                        },
                    }}}},
                    **errors(401, 422),
                },
            }
        },
        "/v1/tasks/claim": {
            "post": {
                "summary": "Drain a channel and claim the next unclaimed message (verb-style)",
                "description": (
                    "Atomically discovers and claims the next unclaimed message on the named channel. "
                    "Returns {claimed: null} if the channel is empty — callers do not need to branch on 409."
                ),
                "operationId": "taskClaim",
                "security": auth_required,
                "tags": ["Tasks"],
                **hints(
                    operation_id="taskClaim",
                    when_to_use="in worker loops — call repeatedly to claim the next available task; the response cleanly distinguishes 'got a task' from 'nothing to do'",
                    output_type="claim_status",
                    prompt="Claim the next available task on the 'jobs' channel as actor 'worker-1'.",
                    agent_prompt_snippet="Call taskClaim with {\"channel\": \"<channel-name>\", \"actor\": \"<your-actor>\"} in a loop. When the response is {claimed: null}, sleep briefly then retry.",
                ),
                **json_body(
                    {
                        "type": "object",
                        "required": ["channel", "actor"],
                        "properties": {
                            "channel": {"type": "string"},
                            "actor": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                    },
                    example={"channel": "jobs", "actor": "worker-1"},
                ),
                "responses": {
                    "200": {"description": "Claim attempt", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "claimed": {"oneOf": [{"type": "null"}, {"$ref": "#/components/schemas/Message"}]},
                            "note": {"type": "string"},
                        },
                    }}}},
                    **errors(401, 403, 404, 422),
                },
            }
        },
        "/v1/tasks/subscribe": {
            "post": {
                "summary": "Read recent messages from a channel (verb-style)",
                "description": "Verb-style alias for GET /v1/channels/{id}/messages. Body parameters instead of query string.",
                "operationId": "taskSubscribe",
                "security": auth_required,
                "tags": ["Tasks"],
                **hints(
                    operation_id="taskSubscribe",
                    when_to_use="to poll a channel for new messages with a body-driven request — useful when channel/since/limit are passed as variables",
                    output_type="message_list",
                    prompt="Subscribe to the 'events' channel and return messages since the last cursor.",
                ),
                **json_body(
                    {
                        "type": "object",
                        "required": ["channel"],
                        "properties": {
                            "channel": {"type": "string"},
                            "since": {"type": ["string", "null"], "format": "date-time"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                        },
                    },
                    example={"channel": "events", "since": None, "limit": 50},
                ),
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "data": {"type": "array", "items": {"$ref": "#/components/schemas/Message"}},
                            "limit": {"type": "integer"},
                            "next_cursor": {"type": ["string", "null"], "format": "date-time"},
                        },
                    }}}},
                    **errors(401, 403, 404, 422),
                },
            }
        },
        "/v1/tasks/post-with-result": {
            "post": {
                "summary": "Post a task and create a paired result channel",
                "description": (
                    "Combines createChannel + createMessage + a deterministic broadcast result channel. "
                    "The producer can then call GET /v1/tasks/{message_id}/result to await; the consumer that "
                    "claims the task calls POST /v1/tasks/{message_id}/result to publish the result."
                ),
                "operationId": "taskPostWithResult",
                "security": auth_required,
                "tags": ["Tasks"],
                **hints(
                    operation_id="taskPostWithResult",
                    when_to_use="for request/response style workflows where the caller wants a single message_id it can wait on for the result",
                    output_type="task_with_result",
                    prompt="Post a task to 'jobs' and wait for the result agent to publish back.",
                    agent_prompt_snippet="Call taskPostWithResult, then poll taskAwaitResult with the returned message.id until the consumer publishes.",
                ),
                **json_body(
                    {
                        "type": "object",
                        "required": ["channel", "content"],
                        "properties": {
                            "channel": {"type": "string"},
                            "content": {"type": "string"},
                            "actor_label": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                    },
                    example={"channel": "jobs", "content": "Summarize doc 42", "actor_label": "caller"},
                ),
                "responses": {
                    "201": {"description": "Posted", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "message": {"$ref": "#/components/schemas/Message"},
                            "channel": {"type": "string"},
                            "result_channel": {"type": "string"},
                            "result_url": {"type": "string"},
                        },
                    }}}},
                    **errors(401, 422),
                },
            }
        },
        "/v1/tasks/{message_id}/result": {
            "post": {
                "summary": "Publish a result for a task posted with post-with-result",
                "description": "The consumer that claimed the task publishes its result here. The paired result channel was created by taskPostWithResult.",
                "operationId": "taskPublishResult",
                "security": auth_required,
                "tags": ["Tasks"],
                **hints(
                    operation_id="taskPublishResult",
                    when_to_use="after claiming and processing a task posted via taskPostWithResult, to deliver the result back to the producer",
                    output_type="message_envelope",
                    prompt="Publish the summary back to the caller as the result of task msg_xyz789.",
                ),
                "parameters": [message_id_param],
                **json_body(
                    {
                        "type": "object",
                        "required": ["content"],
                        "properties": {
                            "content": {"type": "string"},
                            "actor_label": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                    },
                    example={"content": "Summary: …", "actor_label": "worker-1"},
                ),
                "responses": {
                    "201": {"description": "Result published", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "result_channel": {"type": "string"},
                            "message": {"$ref": "#/components/schemas/Message"},
                        },
                    }}}},
                    **errors(401, 403, 404, 422),
                },
            },
            "get": {
                "summary": "Await the result of a task posted with post-with-result",
                "description": "Non-blocking: returns 404 with code result_not_ready if no result has been published yet. Callers poll with backoff.",
                "operationId": "taskAwaitResult",
                "security": auth_required,
                "tags": ["Tasks"],
                **hints(
                    operation_id="taskAwaitResult",
                    when_to_use="after taskPostWithResult, to poll for the consumer's result. On 404 result_not_ready, sleep briefly and retry.",
                    output_type="task_result",
                    prompt="Wait for the result of task msg_xyz789.",
                    agent_prompt_snippet="Call taskAwaitResult repeatedly with exponential backoff until you receive a 200 response with {result: …}.",
                ),
                "parameters": [message_id_param],
                "responses": {
                    "200": {"description": "Result ready", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string"},
                            "result": {"$ref": "#/components/schemas/Message"},
                        },
                    }}}},
                    **errors(401, 403, 404),
                },
            },
        },
        "/v1/sessions": {
            "get": {
                "summary": "List sessions for the calling key",
                "operationId": "listSessions",
                "security": auth_required,
                "tags": ["Sessions"],
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"data": {"type": "array", "items": {"type": "object"}}},
                    }}}},
                    **errors(401),
                },
            },
            "post": {
                "summary": "Create a session",
                "operationId": "createSession",
                "security": auth_required,
                "tags": ["Sessions"],
                **json_body(
                    {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                    },
                    example={"name": "deploy-flow", "metadata": {"run_id": "r-42"}},
                ),
                "responses": {
                    "201": {"description": "Created", "content": {"application/json": {"schema": {"type": "object"}}}},
                    **errors(401, 422),
                },
            },
        },
        "/v1/sessions/{session_id}": {
            "get": {
                "summary": "Get a session",
                "operationId": "getSession",
                "security": auth_required,
                "tags": ["Sessions"],
                "parameters": [session_id_param],
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}, **errors(401, 403, 404)},
            },
            "patch": {
                "summary": "Update a session",
                "operationId": "patchSession",
                "security": auth_required,
                "tags": ["Sessions"],
                "parameters": [session_id_param],
                **json_body({"type": "object", "properties": {"name": {"type": "string"}, "metadata": {"type": "object"}}}),
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}, **errors(401, 403, 404, 422)},
            },
            "delete": {
                "summary": "Delete a session",
                "operationId": "deleteSession",
                "security": auth_required,
                "tags": ["Sessions"],
                "parameters": [session_id_param],
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}}}}}},
                    **errors(401, 403, 404),
                },
            },
        },
        "/v1/observability/metrics": {
            "get": {
                "summary": "Per-key observability metrics",
                "description": "Returns the calling key's request, claim, ack, and error counters. Scoped to the requesting key.",
                "operationId": "observabilityMetrics",
                "security": auth_required,
                "tags": ["Observability"],
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}, **errors(401)},
            }
        },
        "/v1/keys/me": {
            "get": {
                "summary": "Describe the calling API key",
                "description": "Returns the key_id, owner_id, plan, active flag, scopes, and the rate-limit window currently applied to this key.",
                "operationId": "keysMe",
                "security": auth_required,
                "tags": ["Keys"],
                **hints(
                    operation_id="keysMe",
                    when_to_use="to introspect the current key — useful at agent startup to confirm authentication and discover applicable scopes / rate limits",
                    output_type="key_info",
                    prompt="Verify the current API key and report its scopes and rate limit.",
                ),
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "key_id": {"type": "string"},
                            "owner_id": {"type": "string"},
                            "plan": {"type": "string"},
                            "active": {"type": "boolean"},
                            "scopes": {"type": "array", "items": {"type": "string"}},
                            "rate_limit": {"type": "integer"},
                            "rate_limit_window_seconds": {"type": "integer"},
                        },
                    }}}},
                    **errors(401),
                },
            },
            "delete": {
                "summary": "Revoke the calling API key",
                "description": "Permanently deactivates the calling key. Subsequent requests with this key return 401. Use for key rotation: mint a new key, verify it works, then DELETE the old one.",
                "operationId": "deleteKeysMe",
                "security": auth_required,
                "tags": ["Keys"],
                **hints(
                    operation_id="deleteKeysMe",
                    when_to_use="when a key is leaked or during key rotation — after minting a new key and confirming it works, revoke the old one",
                    output_type="confirmation",
                    prompt="Revoke this API key permanently. This cannot be undone.",
                ),
                "responses": {
                    "200": {"description": "Key revoked", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "key_id": {"type": "string"},
                            "revoked": {"type": "boolean"},
                        },
                    }}}},
                    **errors(401),
                },
            },
        },
        "/v1/keys/me/scopes": {
            "put": {
                "summary": "Set the scopes for the calling key",
                "description": "Replaces the scope set for the calling key. Pass the full desired scope list.",
                "operationId": "setKeyScopes",
                "security": auth_required,
                "tags": ["Keys"],
                **json_body(
                    {
                        "type": "object",
                        "required": ["scopes"],
                        "properties": {"scopes": {"type": "array", "items": {"type": "string"}}},
                    },
                    example={"scopes": ["messages:claim", "channels:write"]},
                ),
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "key_id": {"type": "string"},
                            "scopes": {"type": "array", "items": {"type": "string"}},
                        },
                    }}}},
                    **errors(401, 422),
                },
            }
        },
        "/v1/channels/{identifier}/metrics": {
            "get": {
                "summary": "Per-channel metrics (owner only)",
                "description": "Returns counters for the channel: message count, claim count, ack count, etc.",
                "operationId": "channelMetrics",
                "security": auth_required,
                "tags": ["Observability"],
                "parameters": [identifier_param],
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}, **errors(401, 403, 404)},
            }
        },
        "/v1/security/audit": {
            "get": {
                "summary": "List security events for the calling key",
                "description": "Returns recent security-relevant events scoped to the requesting key (key issuance, admin actions involving this key, etc.). A key cannot see events for other keys it does not own.",
                "operationId": "securityAudit",
                "security": auth_required,
                "tags": ["Security"],
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}},
                ],
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "data": {"type": "array", "items": {"type": "object"}},
                            "count": {"type": "integer"},
                        },
                    }}}},
                    **errors(401),
                },
            }
        },
        "/v1/admin/channels/{identifier}/pause": {
            "post": {
                "summary": "Pause a channel (admin only)",
                "description": (
                    "Stops the channel from accepting new messages (writes return 503). Reads continue to work. "
                    "Requires X-Admin-Token matching BACKCHANNEL_ADMIN_TOKEN. Operators use this to quarantine "
                    "a misbehaving channel without dropping data."
                ),
                "operationId": "adminPauseChannel",
                "tags": ["Admin"],
                "security": [],
                "parameters": [
                    identifier_param,
                    {"name": "X-Admin-Token", "in": "header", "required": True, "schema": {"type": "string"}, "description": "Must match the server's BACKCHANNEL_ADMIN_TOKEN env var."},
                ],
                "responses": {"200": {"description": "Paused", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Channel"}}}}, **errors(401, 403, 404)},
            }
        },
        "/v1/admin/channels/{identifier}/resume": {
            "post": {
                "summary": "Resume a paused channel (admin only)",
                "description": "Re-enables writes on a previously paused channel. Requires X-Admin-Token.",
                "operationId": "adminResumeChannel",
                "tags": ["Admin"],
                "security": [],
                "parameters": [
                    identifier_param,
                    {"name": "X-Admin-Token", "in": "header", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "Resumed", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Channel"}}}}, **errors(401, 403, 404)},
            }
        },
    }

    # Merge — /v1/channels/{identifier} and /v1/messages/{message_id} already
    # exist in spec["paths"] for GET/PATCH and POSTs; merge new methods (e.g.
    # DELETE) into the same path object rather than overwriting.
    for path, methods in extra_paths.items():
        if path in spec["paths"]:
            spec["paths"][path].update(methods)
        else:
            spec["paths"][path] = methods

    _inject_idempotency_keys(spec["paths"])

    return spec
