# Semester workspace frontend

## Versions

- `v1/Semester Workspace.dc.html` is the original UI exactly as supplied. It depends on the original custom design-runtime files referenced by that document.
- `index.html`, `styles.css`, and `app.js` began as the v2 standalone quiet Modernist redesign.
- The active V5 interface keeps that shell, adds Voice, integrates the Bencho Checklist with live task data, and adds constraint-aware planning setup.

## Run

From this folder:

```sh
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

The workspace includes responsive Today, Tasks, Calendar, Planner, course, and settings views; task creation and completion; search; local persistence; task details; scheduling; and light and dark themes.

Press Command J on macOS or Control J elsewhere to open Voice. The sheet requests the microphone from that user gesture, passes the actual stream to `VoiceBeam`, and uses the browser's speech-recognition capability for its live transcript. It does not create tasks or send a question anywhere.

The Tasks page uses the MIT-licensed Bencho Checklist with live workspace data. Completing an item updates the same local task state as the detailed list below it.

V5 opens a four-step planning setup for new workspaces. It records weekday and weekend planning windows, meals, recurring commitments, focus-block length, buffer time, a daily study limit, and an optional no-study day. These constraints are stored locally, shown in Today, Calendar, and Planner, and respected by the Schedule action.
