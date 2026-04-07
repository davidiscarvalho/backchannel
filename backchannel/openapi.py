from __future__ import annotations


def build_openapi_spec(onboarding_url: str = "") -> dict:
    channel_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "mode": {"type": "string", "enum": ["broadcast", "claimable"]},
            "access": {"type": "string", "enum": ["open", "restricted"]},
            "description": {"type": "string"},
            "owner_id": {"type": "string"},
            "created_by_key_id": {"type": "string"},
            "metadata_schema": {"type": "object"},
            "pinned_message": {"type": ["string", "null"]},
            "aliases": {"type": "array", "items": {"type": "string"}},
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
            "error": {"type": "string"},
            "message": {"type": "string"},
        },
    }

    auth_required = [{"ApiKeyAuth": []}]

    def json_body(schema: dict) -> dict:
        return {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}}}}

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
                    **json_body({
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
                    }),
                    "responses": {**ok({"$ref": "#/components/schemas/Channel"}, 201), **errors(401, 422)},
                }
            },
            "/v1/channels/{identifier}": {
                "get": {
                    "summary": "Get a channel",
                    "operationId": "getChannel",
                    "security": auth_required,
                    "tags": ["Channels"],
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Channel ID or alias"}],
                    "responses": {**ok({"$ref": "#/components/schemas/Channel"}), **errors(401, 403, 404)},
                },
                "patch": {
                    "summary": "Update a channel",
                    "operationId": "updateChannel",
                    "security": auth_required,
                    "tags": ["Channels"],
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body({
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
                    }),
                    "responses": {**ok({"$ref": "#/components/schemas/Channel"}), **errors(401, 403, 404, 422)},
                },
            },
            "/v1/channels/{identifier}/aliases": {
                "post": {
                    "summary": "Add a channel alias",
                    "operationId": "createChannelAlias",
                    "security": auth_required,
                    "tags": ["Channels"],
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body({"type": "object", "required": ["alias"], "properties": {"alias": {"type": "string"}}}),
                    "responses": {**ok({"$ref": "#/components/schemas/Channel"}, 201), **errors(401, 403, 404, 409, 422)},
                }
            },
            "/v1/channels/{identifier}/invitations": {
                "post": {
                    "summary": "Create a channel invitation (24h expiry)",
                    "operationId": "createChannelInvitation",
                    "security": auth_required,
                    "tags": ["Invitations"],
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
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body({
                        "type": "object",
                        "required": ["content"],
                        "properties": {
                            "content": {"type": "string"},
                            "actor": {"type": "string", "description": "Actor ID or alias"},
                            "actor_label": {"type": "string", "description": "Free-text label when no registered actor"},
                            "metadata": {"type": "object"},
                        },
                    }),
                    "responses": {**ok({"$ref": "#/components/schemas/MessageEnvelope"}, 201), **errors(401, 403, 404, 422)},
                },
                "get": {
                    "summary": "List messages in a channel",
                    "operationId": "listMessages",
                    "security": auth_required,
                    "tags": ["Messages"],
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
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body({"type": "object", "required": ["key_id"], "properties": {"key_id": {"type": "string"}}}),
                    "responses": {**ok({"$ref": "#/components/schemas/Member"}, 201), **errors(401, 403, 404, 422)},
                },
            },
            "/v1/channels/{identifier}/members/{member_key_id}": {
                "delete": {
                    "summary": "Remove a member from a channel (owner only)",
                    "operationId": "removeChannelMember",
                    "security": auth_required,
                    "tags": ["Members"],
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
                    **json_body({
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string", "default": ""},
                            "metadata": {"type": "object", "default": {}},
                        },
                    }),
                    "responses": {**ok({"$ref": "#/components/schemas/Actor"}, 201), **errors(401, 422)},
                }
            },
            "/v1/actors/{identifier}": {
                "get": {
                    "summary": "Get an actor",
                    "operationId": "getActor",
                    "security": auth_required,
                    "tags": ["Actors"],
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
                    "parameters": [{"name": "identifier", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body({"type": "object", "required": ["alias"], "properties": {"alias": {"type": "string"}}}),
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
                    "parameters": [{"name": "invitation_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {**ok({"$ref": "#/components/schemas/Invitation"}), **errors(401, 410, 429)},
                },
                "delete": {
                    "summary": "Revoke a channel invitation",
                    "operationId": "revokeChannelInvitation",
                    "security": auth_required,
                    "tags": ["Invitations"],
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
                    "parameters": [{"name": "message_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body({"type": "object", "required": ["actor"], "properties": {"actor": {"type": "string"}, "metadata": {"type": "object"}}}),
                    "responses": {**ok({"type": "object", "properties": {"status": {"type": "string"}, "message": {"$ref": "#/components/schemas/Message"}}}), **errors(401, 403, 404, 410)},
                }
            },
            "/v1/messages/{message_id}/claim": {
                "post": {
                    "summary": "Claim a message (claimable channels only, first claim wins)",
                    "operationId": "claimMessage",
                    "security": auth_required,
                    "tags": ["Messages"],
                    "parameters": [{"name": "message_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    **json_body({"type": "object", "required": ["actor"], "properties": {"actor": {"type": "string"}, "metadata": {"type": "object"}}}),
                    "responses": {**ok({"type": "object", "properties": {"status": {"type": "string"}, "message": {"$ref": "#/components/schemas/Message"}}}), **errors(401, 403, 404, 409, 410)},
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
