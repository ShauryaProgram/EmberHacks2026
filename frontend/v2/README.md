# Semester workspace frontend

## Versions

- `v1/Semester Workspace.dc.html` is the original UI exactly as supplied. It depends on the original custom design-runtime files referenced by that document.
- `index.html`, `styles.css`, and `app.js` are v2, the standalone quiet Modernist redesign.

## Run v2

From this folder:

```sh
python3 -m http.server 4173
```

Open `http://127.0.0.1:4173`.

V2 includes responsive Today, Tasks, Calendar, Planner, course, and settings views; task creation and completion; search; local persistence; task details; scheduling; and light and dark themes.
