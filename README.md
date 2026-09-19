# Ember

Ember is a local-first U of T study workspace. It syncs Fall 2026 Quercus
courses, assignments, calendar entries, and official section meetings; plans
study blocks around them; and activates the procrastination watcher only while
one of those study blocks is in progress.

## Run the complete product

Python 3.11 and Node.js are required.

```sh
make setup
# Add OPENROUTER_API_KEY to backend/.env for natural-language input and
# model-based focus classification.
make run
```

Open [http://127.0.0.1:8766](http://127.0.0.1:8766). The first screen asks for
a Quercus API token, verifies it with U of T, encrypts it locally, and starts the
initial sync. The same process serves the frontend, API, sync scheduler,
notification dispatcher, study planner, and focus watcher.

macOS asks for Accessibility permission when the watcher first needs a window
title. Screen Recording is only needed for optional screenshot escalation.

## Verify

```sh
make test
```

See `backend/README.md` for sync and API details and
`procasination/README.md` for the watcher privacy model and permissions.
