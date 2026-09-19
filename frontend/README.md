# Ember frontend

## Versions

- `v1/Semester Workspace.dc.html` is the original UI exactly as supplied. It depends on the original custom design-runtime files referenced by that document.
- `v2/` preserves the standalone quiet Modernist redesign that the current shell grew out of.
- `index.html`, `styles.css`, and `app.js` are the active workspace. The V5 interface keeps the v2 shell and adds a daily briefing, persistent Voice controls, and constraint-aware planning setup on top of the live Ember backend.

## Develop

From this folder:

```sh
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`.

Vite proxies `/api` and `/health` to the combined backend on port `8766`. For
the actual product entry point, run `make run` from the repository root; it
builds this frontend and serves it from the same FastAPI process as sync and the
procrastination watcher.

## Data

Every view is backed by the API, not by local seed data. Courses, assignments,
calendar events, and study sessions come from `/api/courses`,
`/api/assignments`, `/api/calendar/events`, and the study planner. Assignment
completion is owned by Quercus and is read-only here.

Use the persistent **Speak to Ember** control, press Command J on macOS, or press
Control J elsewhere to open the voice console and dictate a personal calendar
command. The transcript remains editable, so requests can also be typed when
browser speech recognition is unavailable. **Send** can create one-time or recurring
personal events, delete matching personal events, or answer a date/calendar question.
Browser push permission is likewise requested only after **Enable alerts** is
pressed in Settings.

## Planning constraints

The four-step setup records weekday and weekend planning windows, meals,
recurring commitments, focus-block length, buffer time, a daily study limit, and
an optional no-study day. It opens once after Quercus is connected, and is
reachable afterwards from Settings or the Planner.

The complete V5 profile is written to `/api/study/settings` on save and cached
in `localStorage` for fast startup. The backend is the source of truth after it
has a profile: planning windows, meals, recurring commitments, focus-block
length, buffer time, daily limits, and the no-study day all shape regenerated
study sessions. Those generated sessions are also the schedule followed by the
focus watcher.

## Daily workspace

Today opens with a compact personal briefing, the next focus block, a chronological
agenda, and assignments that need attention. Study-session completion remains in
the Planner, keeping the home page focused on what is happening now.
