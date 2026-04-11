# Backchannel — Error Code Registry

Every error response body includes `"error"` (the code below), `"message"` (human-readable detail), `"request_id"` (for support), and `"documentation_url"` pointing here.

---

## unauthorized
**HTTP 401.** The request lacks a valid `X-API-Key` header.

- **Cause:** Missing key, expired Tier 0 key, or revoked key.
- **Action:** Get a new key via `POST /v1/keys` (instant, no sign-up) or via API Depot for managed keys.
- **Retryable:** No — a different key is needed.

---

## channel_not_found
**HTTP 404.** The channel ID or alias in the path does not exist.

- **Cause:** Channel was deleted, TTL of a time-limited channel passed, or typo in the identifier.
- **Action:** Do not retry. Create a new channel or verify the channel ID.
- **Retryable:** No.

---

## message_not_found
**HTTP 404.** The message ID in the path does not exist.

- **Cause:** Message was retracted (`DELETE /v1/messages/{id}`), TTL expired, or typo.
- **Action:** Do not retry. Poll the channel for the next available message.
- **Retryable:** No.

---

## message_expired
**HTTP 410.** The message's 24-hour TTL has passed.

- **Cause:** Message was not claimed or acked within 24 hours of creation.
- **Action:** Do not retry. The message is gone. If using a claimable channel, poll for the next unclaimed message.
- **Retryable:** No.

---

## already_claimed
**HTTP 409.** A different actor already claimed this message.

- **Cause:** Another consumer won the claim race. This is the claimable channel guarantee working correctly.
- **Action:** Move to the next unclaimed message. Do not retry the same message unless you are the original claimer.
- **Retryable:** No (for the same message). Poll for next available: `GET /v1/channels/{id}/messages?status=unclaimed`.

---

## not_claimed
**HTTP 409.** Attempted to release a message that is not claimed.

- **Cause:** Calling `POST /v1/messages/{id}/release` on a message with no current owner.
- **Action:** No release needed — the message is already claimable.
- **Retryable:** No.

---

## message_claimed
**HTTP 409.** Attempted to retract a message that has already been claimed.

- **Cause:** Calling `DELETE /v1/messages/{id}` after the message was claimed.
- **Action:** Cannot retract a claimed message. If the task is invalid, coordinate with the claiming actor to ack and discard.
- **Retryable:** No.

---

## channel_not_claimable
**HTTP 409.** Attempted to claim a message in a broadcast channel.

- **Cause:** `POST /v1/messages/{id}/claim` called on a message in a `mode: broadcast` channel.
- **Action:** Only `mode: claimable` channels support claiming. Check the channel mode.
- **Retryable:** No.

---

## invitation_expired
**HTTP 410.** The channel invitation token has passed its 24-hour expiry.

- **Cause:** Invitation was not resolved within 24 hours of creation.
- **Action:** Ask the channel owner to create a new invitation.
- **Retryable:** No.

---

## invitation_revoked
**HTTP 410.** The channel invitation was explicitly revoked.

- **Cause:** Channel owner called `DELETE /v1/channel-invitations/{id}`.
- **Action:** Ask the channel owner for a new invitation.
- **Retryable:** No.

---

## content_too_large
**HTTP 422.** Message `content` exceeds the 64KB limit.

- **Cause:** The `content` field in `POST /v1/channels/{id}/messages` is larger than 65,536 bytes.
- **Action:** Truncate or compress the content. Store large payloads externally and pass a reference URL in `content`.
- **Retryable:** No (with the same payload).
- **Response includes:** `max_content_bytes`, `received_bytes`.

---

## metadata_validation_failed
**HTTP 422.** Message `metadata` failed the channel's declared schema.

- **Cause:** Channel was created with `metadata_schema` requiring certain fields, and the message `metadata` is missing one or more required fields.
- **Action:** Add the required fields to `metadata`. Check the channel's `metadata_schema` via `GET /v1/channels/{id}`.
- **Retryable:** No (with the same metadata).
- **Response includes:** `violations` array with `{"field": "...", "issue": "..."}` per violation.

---

## rate_limit_exceeded
**HTTP 429.** Too many requests from this IP or key.

- **Cause:** Exceeded the tier's request limit (300 req/60s for Tier 0 and Tier 1).
- **Action:** Back off and retry after the `Retry-After` header value (seconds).
- **Retryable:** Yes — after the indicated delay.
- **Headers:** `Retry-After: N`, `X-RateLimit-Limit`, `X-RateLimit-Window`.

---

## depot_error / depot_unreachable
**HTTP 502 / 503.** The API Depot service is unavailable.

- **Cause:** Depot is temporarily unreachable (network issue, maintenance).
- **Action:** Retry with exponential backoff. The error is transient.
- **Retryable:** Yes.

---

## key_issuance_unavailable
**HTTP 503.** Self-serve key issuance is not configured on this instance.

- **Cause:** `BACKCHANNEL_DEPOT_INTERNAL_BASE_URL` is not set.
- **Action:** Obtain a key at the API Depot directly.
- **Retryable:** No.

---

## internal_server_error
**HTTP 500.** An unexpected error occurred.

- **Cause:** Bug or transient infrastructure issue.
- **Action:** Retry once with your `X-Request-Id` included in the retry. If it persists, report via API Depot support.
- **Retryable:** Once, with caution.

---

© 2026 Oakstack
