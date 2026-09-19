# Ordo Backend

Ordo's per-user backend ingests Quercus (Canvas), stores a normalized calendar in SQLite, produces preparation reminders, and sends browser Web Push notifications.

## What It Syncs

- Fall 2026 Quercus courses only, including the student's enrolled lecture/tutorial/practical sections
- Published assignments, due-date changes, submission state, rubrics, and submission methods
- Canvas calendar events
- Official U of T Timetable Builder meetings for the student's exact Quercus sections, plus recurring Canvas course events
- Canvas Planner items, including quizzes, discussions, pages, notes, sub-assignments, and assessment requests
- Announcements, including changed announcements
- Course syllabuses, home pages, pages, module outlines, and likely syllabus/schedule PDF, DOCX, or text files
- Dated obligations and class changes found in unstructured content by OpenRouter
- Constraint-based study sessions spread across the days before each assignment, quiz, test, writing task, or project

Structured Canvas data always wins. A batched metadata/excerpt pass filters changed sources before deeper extraction. OpenRouter sees only newly changed source text, never the API token, and model actions are rejected unless they contain an exact source quote and a valid ISO-8601 date. Ordinary assignments with a structured due date do not need a model call.

## Run It

Python 3.11 or newer is supported.

```sh
cd backend
make setup
# Put your key in .env as OPENROUTER_API_KEY=...
make test
make run
```

OpenAPI docs are available at [http://127.0.0.1:8766/docs](http://127.0.0.1:8766/docs). Data and locally generated encryption/VAPID keys are written under `backend/data/` and ignored by git.

## Terminal Client

With the backend running in one terminal, onboarding is interactive and keeps the token out of shell history:

```sh
make onboard
make doctor
make status
make sync
```

Browse the synchronized data without a frontend:

```sh
.venv/bin/python -m app.cli courses
.venv/bin/python -m app.cli assignments
.venv/bin/python -m app.cli calendar --days 60
.venv/bin/python -m app.cli announcements --unread
.venv/bin/python -m app.cli reminders --status pending
```

Every listing command supports `--json`. If the backend runs on another port, place `--api URL` before the command, for example `python -m app.cli --api http://127.0.0.1:9000 status`.

## Frontend Flow

1. `POST /api/onboarding` with `quercus_api_token`, `timezone`, and optional `reminder_offsets_days`. The token is verified against `/api/v1/users/self`, encrypted at rest, and an initial sync begins.
2. Poll `GET /api/sync/status` until the first run succeeds. Use `POST /api/sync` for a user-triggered refresh.
3. Render `GET /api/calendar/events?start=...&end=...`. Quercus events are read-only; frontend-created events use `POST /api/calendar/events` and may be edited normally.
4. Render or manage `GET /api/reminders`, `GET /api/assignments`, `GET /api/courses`, and `GET /api/announcements`.
5. Fetch `GET /api/notifications/vapid-public-key`, create a browser `PushSubscription`, then send it to `POST /api/notifications/subscriptions`.

The service polls Quercus hourly and dispatches due notifications every 30 seconds. Canvas Live Events/webhooks require institution-level setup and are not a dependable student-token feature, so polling is the reliable default. Polling is incremental in effect: stable IDs and content hashes mean only new or changed text is analyzed. The target term defaults to Fall 2026 through `ACADEMIC_TERM_SEASON` and `ACADEMIC_TERM_YEAR`.

Course meetings appear in `/api/calendar/events` with `source_type: "course_meeting"` and a `kind` of `lecture`, `tutorial`, `practical`, or `class`. A frontend can request only meetings with `/api/calendar/events?source_type=course_meeting`. The sync matches Quercus section names such as `LEC0101` and `PRA0104` against the institution-operated U of T Timetable Builder API, then expands the official weekly times into dated calendar events. It excludes Thanksgiving and the Fall reading week by default; the term bounds and breaks are configurable in `.env`.

## Study Planning

The study planner is deterministic rather than model-generated. It estimates effort from assignment type, points, and description; starts larger work earlier; spreads sessions across different days; and schedules around course meetings, personal events, and other calendar commitments. Defaults limit weekdays to four study hours, weekends to five hours, sessions to 90 minutes, and planning to waking hours with 15-minute buffers. Study sessions carry `source_type: "study_plan"`, include their target assignment in metadata, and receive a 15-minute reminder.

`POST /api/input` accepts calendar commands such as `I have a dentist appointment next Tuesday from 3 to 4 PM`, `Gym every weekday at 7 AM`, or `Delete my dentist appointment`. Gemini receives the profile's authoritative local date, time, weekday, and timezone, then returns validated create/delete actions. Recurrences can be daily, weekdays, weekends, or selected weekly days and are bounded to one year. Only personal events can be deleted; synced Quercus, course, and study events remain read-only. Backend code applies the actions and recomputes study sessions from the earliest affected day. Ambiguous requests return `needs_clarification: true` without changing the calendar.

Study availability is configurable through `GET/PATCH /api/study/settings`. The V5 `planning_profile` is the server-side source of truth for weekday and weekend windows, meals, weekly commitments, focus-block length, buffer time, daily limits, and an optional no-study day. Meals and commitments are expanded into protected time in the user's timezone, with the selected buffer on both sides, whenever the plan is rebuilt. The frontend can read sessions from `/api/study/plan`, request a fresh plan with `POST /api/study/recompute`, and mark sessions active, completed, or skipped through `PATCH /api/study/sessions/{event_id}`. Skipping a session reschedules its remaining work.

## Main Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/onboarding` | Verify and store a Quercus token |
| `GET` | `/api/onboarding/status` | Read the local profile, never the token |
| `POST` | `/api/sync` | Start an immediate sync |
| `GET` | `/api/sync/status` | Latest run, warnings, and analysis queue |
| `GET` | `/api/calendar/events` | Calendar range query |
| `POST` | `/api/calendar/events` | Create a frontend/manual event |
| `GET` | `/api/assignments` | Assignment/submission records |
| `GET` | `/api/announcements` | Announcement inbox |
| `GET` | `/api/reminders` | Notification schedule |
| `POST` | `/api/input` | Ask calendar questions or create/delete personal and recurring events |
| `GET` | `/api/study/plan` | Generated study sessions |
| `POST` | `/api/study/recompute` | Recompute an affected planning window |
| `GET/PATCH` | `/api/study/settings` | V5 planning profile, protected time, and workload limits |
| `POST` | `/api/notifications/subscriptions` | Register a browser PushSubscription |

## Reliability And Security

- Canvas pagination is accepted only from `https://q.utoronto.ca/api/v1/`.
- The API token is Fernet-encrypted with a mode-600 local key and is never returned by an endpoint.
- A failed/partial assignment fetch does not delete known work. Missing assignments are retired only after a complete course assignment fetch.
- Assignment and reminder identities stay stable across reschedules, so frontend state does not duplicate.
- Submitted, excused, deleted, and cancelled work closes its generated reminders.
- Syllabus files are limited by type and size. External course links and LTI launches are not crawled.
- Without an OpenRouter key, deterministic assignment/calendar syncing continues and changed unstructured sources remain queued.

For a public deployment, put authentication and TLS in front of this service. It intentionally defaults to `127.0.0.1` because the current design is one local backend per user.
