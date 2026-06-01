<!--
Thanks for the PR. A few things that make review faster:

- One change per PR (feature, fix, or refactor — pick one).
- CI must be green before review.
- Read CONTRIBUTING.md if this is your first contribution.
-->

## What

<!-- One paragraph: what does this PR change? -->

## Why

<!-- The motivation. Link the related issue if there is one (Closes #123). -->

## How

<!-- One paragraph on the approach. Call out anything non-obvious or any
trade-off you considered. -->

## Verification

<!-- What did you do to convince yourself this works? Paste output where
relevant. At minimum: -->

- [ ] `pytest tests/ mcp_server/tests/` is green.
- [ ] `cd ui && npm run build` succeeds (if the change touches `ui/`).
- [ ] `docker compose -f docker-compose.self-host.yml up -d --build`
      still comes up healthy (if the change touches the self-host path).
- [ ] Manually exercised on a running instance (paste the `curl` or
      describe the click-path).

## Notes for the reviewer

<!-- Anything I should know before reading the diff. Optional. -->
