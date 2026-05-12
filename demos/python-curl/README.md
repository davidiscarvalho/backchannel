# Backchannel — pure curl demo

The whole protocol in four shell calls. No SDK, no Python, no Node, no
dependencies beyond `curl` and `jq`.

```bash
./run.sh
```

What you see:

```
▸ 1/4  mint producer key
   producer key: bck_xxxxxxxxxxxxxxx…
▸ 2/4  create claimable channel
   channel id: ch_…
▸ 3/4  post task
   message id: msg_…
▸ 4a/4 mint worker key + actor
▸ 4b/4 claim + ack
{ "status": "claimed",       "claimed": { "id": "act_…", "name": "worker-1" } }
{ "status": "acknowledged",  "acked":   [ { "name": "worker-1", ... } ] }
✓ end-to-end handoff complete
```

That's the entire feature surface — every other demo in this repo is
just a friendlier wrapper around these four calls.

## Self-hosted

```bash
BACKCHANNEL_BASE_URL=http://localhost:8080 ./run.sh
```
