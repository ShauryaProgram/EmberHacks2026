# Ordo

**An intelligent academic workspace that turns coursework into an automated, time-blocked schedule built around your real life.**

Ordo — Latin for *order* — takes the scattered deadlines of a semester and arranges
them into a clear daily plan: study blocks placed around classes, meals, sleep,
and commitments, updated by voice, and quietly protected from distraction.

Powered by **Gemini** throughout.

---

## Gemini-powered features

### Grounded extraction from messy course content

Assignments and calendar entries are structured; syllabi, announcements, and
course pages are not — deadlines hide in prose. Gemini reads changed
unstructured sources and extracts dated obligations and class changes into
validated actions.

Every response is constrained by a strict JSON schema. An extracted action must
carry a valid ISO timestamp **and verbatim evidence quoted from the source**
before it is allowed to touch the calendar. Nothing is created on guesswork.

### Intelligent priority and workload engines

Gemini analyses raw assignment descriptions to estimate realistic completion
times, and ranks what to work on first using grade weight, hard deadlines, and
prerequisite chains — so the plan reflects what actually matters this week.

### Voice-driven scheduling (⌘J)

Press ⌘J and talk. A live microphone stream runs through Gemini 3.8 Flash,
parsing conversational speech — *"move my writing prep after dinner, push gym
back 30 minutes, and protect my lunch"* — into validated create, update, or
delete actions that mutate the timeline directly. No chat window, no forms.

Ambiguous requests return a clarifying question instead of changing your data.

### Semantic anti-procrastination

During an active study block, Ordo reads active window and tab metadata and asks
Gemini to classify it as on-task, off-task, or unclear.

This is the difference between a URL blocklist and understanding context: a
linear algebra lecture on YouTube is permitted; gaming clips and social feeds
earn a gentle nudge to refocus or reschedule. Genuine work for a *different*
course is recognised as work, not distraction.

- Runs only while a planned study block is in progress
- Local rules and cached results are tried first
- Screenshots are escalated to only when text metadata is insufficient
- The interruption decision is made by deterministic code — the model advises,
  it does not interrupt you

### Two models, one integration

OpenRouter lets Ordo route two workloads to the Gemini model each deserves:

| Workload | Model |
| --- | --- |
| Structured extraction, calendar actions, vision escalation | `google/gemini-3.8-flash` |
| Recurring low-latency focus classification | `google/gemini-3.1-flash-lite` |

Both are configurable by environment variable.

---

## The rest of the workspace

- **Human-first onboarding** — planning hours, breakfast/lunch/dinner locks,
  outside commitments, focus block lengths, daily study limits, and an optional
  no-study day. Study blocks may never overwrite a protected anchor.
- **Calendar view** — a weekly time-grid of classes, study blocks, and protected
  routines.
- **Planner view** — a 7-day operational ledger with permitted planning windows
  and a shelf of unscheduled work.
- **Today dashboard** — a "Happening now" card with live countdown, open-task
  counters, planned focus time, next-class summary, and an animated Bencho
  checklist wired to real task data.
- **Deterministic planning** — placement itself is a mathematical interval
  planner in Python, not model output. It starts large tasks early, spreads
  sessions across days, and treats sleep, meals, buffers, and daily limits as
  non-negotiable. Schedules stay explainable, testable, and safe to recompute.

---

## Run it

Python 3.11 and Node.js required.

```sh
make setup
# Add OPENROUTER_API_KEY to backend/.env to enable the Gemini features above.
make run
```

Open [http://127.0.0.1:8766](http://127.0.0.1:8766). The first screen walks you
through onboarding and the initial sync. A single process serves the frontend,
API, sync scheduler, notification dispatcher, study planner, and focus watcher.

On macOS, Accessibility permission is requested the first time the watcher needs
a window title. Screen Recording is only needed for optional screenshot
escalation.

### Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | Enables all Gemini-backed features | — |
| `OPENROUTER_MODEL` | Extraction and natural-language model | `google/gemini-3.8-flash` |
| `PROCRASTINATION_TEXT_MODEL` | Focus classification from metadata | `google/gemini-3.1-flash-lite` |
| `PROCRASTINATION_VISION_MODEL` | Screenshot escalation | `google/gemini-3.8-flash` |

### Verify

```sh
make test
```

---

## What's next

- **Adaptive velocity learning** — compare actual focus time to estimated
  durations per course and fine-tune effort estimation to each student's pacing.
- **Native background daemon** — a lightweight tray utility for macOS and
  Windows so the focus watcher runs without browser dependency.
- **Bi-directional submission tracking** — mark study sessions complete on
  submission and rebalance the remaining week automatically.
- **Mobile companion** — Web Push alerts and an on-the-go Today view.

---

See `backend/README.md` for API details and `procasination/README.md` for the
watcher's privacy model and permissions.
