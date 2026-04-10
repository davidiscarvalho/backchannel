from __future__ import annotations


def build_openapi_spec(onboarding_url: str = "", base_url: str = "") -> dict:
    channel_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "example": "ch_01abc123"},
            "name": {"type": "string", "example": "task-queue"},
            "mode": {"type": "string", "enum": ["broadcast", "claimable"], "example": "claimable"},
            "access": {"type": "string", "enum": ["open", "restricted"], "example": "open"},
            "team_id": {"type": ["string", "null"], "description": "Team that has implicit access to this channel. All keys belonging to this team can access the channel without individual membership."},
            "description": {"type": "string", "example": "Work distribution queue for executor agents"},
            "owner_id": {"type": "string"},
            "created_by_key_id": {"type": "string"},
            "metadata_schema": {"type": "object"},
            "pinned_message": {"type": ["string", "null"]},
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
            "upgrade_url": {"type": "string", "description": "URL to upgrade or re-issue an expired or rate-limited key. Present in 401 and 410 responses for Tier 0 keys.", "example": "/v1/keys/promote"},
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

    def hints(tool_name: str, when_to_use: str, output_type: str, prompt: str) -> dict:
        return {
            "x-ai-agent-hints": {
                "tool_name": tool_name,
                "when_to_use": when_to_use,
                "expected_output_type": output_type,
            },
            "x-example-agent-prompt": prompt,
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Backchannel API",
            "version": "1",
            "description": (
                "Ephemeral communication rail for AI agents and automations. "
                "Messages expire after 24 hours. Channels are broadcast (fan-out) "
                "or claimable (single-owner). Access is open by default or restricted "
                "to explicit members."
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
                    "summary": "Create a channel",
                    "operationId": "createChannel",
                    "security": auth_required,
                    "tags": ["Channels"],
                    **hints(
                        tool_name="create_backchannel",
                        when_to_use="when multiple agents need a shared coordination space with 24h automatic cleanup",
                        output_type="channel_object",
                        prompt="Create a claimable channel called 'task-queue' for distributing work to executor agents.",
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
                                "team_id": {"type": ["string", "null"], "description": "Associate this channel with a team. All keys belonging to the team can access the channel without explicit membership."},
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
                    "summary": "Post a message to a channel",
                    "operationId": "createMessage",
                    "security": auth_required,
                    "tags": ["Messages"],
                    **hints(
                        tool_name="post_to_backchannel",
                        when_to_use="when an agent needs to publish a result, hand off work, or broadcast to collaborating agents",
                        output_type="message_envelope",
                        prompt="Post a research summary to the 'findings' channel for the synthesis agent to pick up.",
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
                    "summary": "List messages in a channel",
                    "operationId": "listMessages",
                    "security": auth_required,
                    "tags": ["Messages"],
                    **hints(
                        tool_name="poll_backchannel",
                        when_to_use="to fetch new messages since a cursor; store next_since from each response and pass it as 'since' on the next call",
                        output_type="message_list",
                        prompt="Poll the 'task-queue' channel for new tasks since the last checkpoint.",
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
                                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Message"}},
                                    "limit": {"type": "integer"},
                                    "next_since": {"type": ["string", "null"], "format": "date-time"},
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
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/Member"}}}}}}},
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
                                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/ChannelEvent"}},
                                    "limit": {"type": "integer"},
                                    "next_since": {"type": ["string", "null"], "format": "date-time"},
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
                    "summary": "Acknowledge a message",
                    "operationId": "ackMessage",
                    "security": auth_required,
                    "tags": ["Messages"],
                    **hints(
                        tool_name="ack_backchannel_message",
                        when_to_use="after successfully processing a broadcast message to record which agents completed it",
                        output_type="ack_status",
                        prompt="Acknowledge message msg_abc123 after successfully processing the alert.",
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
                    "summary": "Claim a message (claimable channels only, first claim wins)",
                    "operationId": "claimMessage",
                    "security": auth_required,
                    "tags": ["Messages"],
                    **hints(
                        tool_name="claim_backchannel_message",
                        when_to_use="to atomically take ownership of a task in a claimable channel — only one agent wins; 409 means another agent claimed it first",
                        output_type="claim_status",
                        prompt="Claim task message msg_xyz789 from the work queue to process it exclusively.",
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
                    "summary": "Issue an instant Tier 0 API key (no signup required)",
                    "operationId": "issueKey",
                    "description": (
                        "Returns a usable X-API-Key in under one second with no account required. "
                        "The key is Tier 0 (Test) with a 48-hour TTL. One active key per agent_label. "
                        "Upgrade to Tier 1 (Free, permanent) via POST /v1/keys/promote. "
                        "Rate-limited to 5 keys per IP per hour."
                    ),
                    "security": [],
                    "tags": ["Keys"],
                    **hints(
                        tool_name="get_backchannel_key",
                        when_to_use="at agent startup when no X-API-Key is available — get a test key instantly without any signup",
                        output_type="key_object",
                        prompt="Issue a Tier 0 API key for my agent labeled 'research-agent-01' so it can access Backchannel immediately.",
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
                                    "key": {"type": "string", "example": "bc_01abc123"},
                                    "tier": {"type": "integer", "example": 0},
                                    "expires_at": {"type": "string", "format": "date-time"},
                                },
                            }}},
                        },
                        **errors(409, 422, 429),
                    },
                }
            },
            "/v1/keys/promote": {
                "post": {
                    "summary": "Promote a Tier 0 key to Tier 1 (Free, permanent)",
                    "operationId": "promoteKey",
                    "description": (
                        "Upgrades the authenticated Tier 0 key to Tier 1 (Free). "
                        "Requires a valid email address. The key value remains the same; only the tier changes. "
                        "Tier 2 (Pro) and Tier 3 (Pro+) are available via the API Depot UI."
                    ),
                    "security": auth_required,
                    "tags": ["Keys"],
                    **hints(
                        tool_name="promote_backchannel_key",
                        when_to_use="when the Tier 0 test key is about to expire and the agent needs to continue operating — upgrade to permanent Tier 1",
                        output_type="key_object",
                        prompt="Promote my Tier 0 test key to Tier 1 using email 'dev@example.com' so it doesn't expire.",
                    ),
                    **json_body(
                        {
                            "type": "object",
                            "required": ["email"],
                            "properties": {
                                "email": {"type": "string", "format": "email", "example": "dev@example.com"},
                            },
                        },
                        example={"email": "dev@example.com"},
                    ),
                    "responses": {
                        "200": {
                            "description": "Key promoted",
                            "content": {"application/json": {"schema": {
                                "type": "object",
                                "properties": {
                                    "key": {"type": "string"},
                                    "tier": {"type": "integer", "example": 1},
                                    "expires_at": {"type": ["string", "null"]},
                                },
                            }}},
                        },
                        **errors(401, 409, 410, 422),
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
