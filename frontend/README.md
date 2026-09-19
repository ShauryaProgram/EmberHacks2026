# Semester workspace frontend

## Versions

- `v1/Semester Workspace.dc.html` is the original UI exactly as supplied. It depends on the original custom design-runtime files referenced by that document.
- `index.html`, `styles.css`, and `app.js` are the v2 standalone quiet Modernist redesign.
- The active v4 entry adds a React voice transcript sheet without changing the v2 page shell.

## Run v4

From this folder:

```sh
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

V4 keeps the responsive Today, Tasks, Calendar, Planner, course, and settings views; task creation and completion; search; local persistence; task details; scheduling; and light and dark themes.

Press Command J on macOS or Control J elsewhere to open a transcript-only voice sheet. The sheet requests the microphone from that user gesture, passes the actual stream to `VoiceBeam`, and uses the browser's speech-recognition capability for its live transcript. It does not create tasks or send a question anywhere.
