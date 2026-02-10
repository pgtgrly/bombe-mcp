# Bombe VS Code Integration (Scaffold)

This folder contains a minimal extension scaffold for integrating VS Code with Bombe.

## Current scope

- Command registration stub (`bombe.status`).
- Extension entrypoint (`extension.js`).
- Placeholder scripts in `package.json`.

## Next implementation steps

1. Add a VS Code task/command to launch `python -m bombe.server`.
2. Add settings for repository root, runtime profile, and diagnostics limit.
3. Add status panel rendering for `bombe status` and `bombe doctor`.
