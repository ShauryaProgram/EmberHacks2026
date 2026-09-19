# Semester workspace frontend

## Versions

- `v1/Semester Workspace.dc.html` is the original UI exactly as supplied. It depends on the original custom design-runtime files referenced by that document.
- `index.html`, `styles.css`, and `app.js` are the v2 standalone quiet Modernist redesign.
- The active v3 entry adds a React voice-input island without changing the v2 page shell.

## Run v3

From this folder:

```sh
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

V3 keeps the responsive Today, Tasks, Calendar, Planner, course, and settings views; task creation and completion; search; local persistence; task details; scheduling; and light and dark themes.

The New task dialog now includes a UI-only VoiceBeam preview from `voice-glow`. Listen and Stop animate the component locally so the interaction can be reviewed without requesting microphone permission or connecting speech recognition yet.
