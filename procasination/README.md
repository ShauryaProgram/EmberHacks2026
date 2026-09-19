# Procrastination Watcher

Watches the student's screen **only while a scheduled study session is in
progress**, judges whether what is on screen advances the assignment that
session was created for, and nudges with one concrete way back in.

Nothing under `backend/` is modified. This package reads the backend's database
and reuses its `PushService`.

## Why text first, not screenshots

The main loop never looks at pixels. Every few seconds it reads, in about 5ms
and zero tokens:

- the frontmost application name
- **the window title** — this is the signal. `Integration by parts — YouTube`
  and `Minecraft 100 Days — YouTube` are trivially separable, and titles carry
  most of what a screenshot would tell you
- the active browser tab's host
- seconds since the last keyboard or mouse event

That is ~40 tokens per reading against ~1100 for a downscaled screenshot, and it
means no pixels of the student's DMs, banking, or password manager are ever sent
anywhere. A screenshot is taken **only** when the text genuinely did not settle
it — a fullscreen video with no title, a PDF called `document.pdf` — or to
confirm an accusation before interrupting someone.

## Three tiers, cheapest first

This mirrors the triage-then-analyze shape the Canvas sync already uses.

1. **Local rules, no model call** (`segments.py`). Long idle, a code editor, a
   game launcher, a known coursework or entertainment site. Deliberately tight:
   a rule here spends nothing but can also be confidently wrong, so anything
   dual-purpose — YouTube, Notion, Discord, Reddit, Spotify, Preview — is left
   to the model, which gets to read the title and knows what is due.
2. **Per-session verdict cache**. A student flips between the same few windows
   for an hour, so after the first minutes almost every cycle resolves for free.
   Scoped to one session on purpose: "on task" only means something relative to
   one assignment. Ambiguity is never cached, so it can escalate later.
3. **Gemini.** A batched text pass once a minute over unresolved segments; a
   screenshot pass only for what that could not settle.

## Browsers

Chrome, Safari, Edge, Arc, Brave and other Chromium forks expose their active
tab's URL to AppleScript, so the host is available for the local rules.

**Firefox and its forks (Zen, LibreWolf, Waterfox, Tor) expose no scriptable tab
URL on macOS.** They are recognised so the watcher skips a lookup that would cost
a subprocess and an Automation prompt to learn nothing, and it leans on the
window title instead — which is the signal that matters anyway:
`Epsilon-delta proofs - YouTube` and `Minecraft 100 Days - YouTube` are separable
with no URL at all. A title too generic to judge escalates to a screenshot, and
an untitled browser window skips the text pass entirely and goes straight there,
since there is nothing for a text model to read.

## Models

Both Gemini on OpenRouter.

| role | model | why |
| --- | --- | --- |
| every-minute text verdict | `google/gemini-3.1-flash-lite` | latency, and it is the call that repeats |
| screenshot + nudge wording | `google/gemini-3.8-flash` | multimodal, and writing to a person needs the reasoning |

Every call runs at `reasoning: {"effort": "low"}`. Measured on
`gemini-3.8-flash`: the default thinking budget spent ~1175 output tokens
reaching the same verdict low effort reaches in ~99 — on the priciest dimension,
and the reasoning was what exhausted `max_tokens` mid-JSON. Reasoning cannot be
disabled on this endpoint, only turned down.

A 2-hour session costs a few cents.

## What is guaranteed

- **The model never decides to interrupt.** It supplies verdicts and wording;
  whether to nudge is `_nudge_blocked()` in `monitor.py` — sustained drift, a
  confident source, cooldown elapsed, session not ending.
- **No nudge on a shaky verdict.** A low-confidence `off_task` from the text
  model is either confirmed by a screenshot or dropped. A false accusation kills
  the feature's credibility instantly.
- **An unreachable model produces no verdict**, and therefore no nudge. If only
  the wording call fails, a deterministic fallback nudge is sent instead, built
  from facts already in hand.
- **Window titles are untrusted input.** A web page chooses its own title, so
  every prompt states that titles are data and not instructions, matching how
  the Canvas sync treats course content. Verdicts for keys that were not asked
  about are discarded; confidence and category are clamped locally.
- **Private apps are never judged or photographed** (`PROCRASTINATION_SENSITIVE_APPS`).
  The app name is still recorded so the timeline stays honest about the gap.
- Only the URL **host** is ever sent, never the path or query.
- A skipped or completed study session is not watched — the student already said
  where they stand.
- **Alt-tabbing is caught.** On-task time decays the drift clock second for
  second rather than zeroing it. Flicking between the assignment and a
  distraction is the most common shape procrastination takes, and a reset would
  mean a fifteen-second glance at the editor erases a minute of drift.
- **Self-changing title decoration is stripped.** `(248) YouTube` becomes
  `(249) YouTube` on its own while the page sits untouched; a terminal spinner
  animates its title frame by frame. Treating either as new context would thrash
  the verdict cache and re-bill every notification.

## Wrong subject counts as off task

A study block is scheduled for one specific assignment, so working on a
*different* course during it is off task — the student planned to do this thing
now. That is a deliberate product decision, not a misfire.

But it is not procrastination, and it is never worded as though it were. The
model is given the student's enrolled course codes so it can tell another
course's work apart from something merely academic-looking, and tags it
`other_coursework`. The nudge then acknowledges the work and redirects:

> **Switching back to MAT137 PCQ3** — You are making progress on your CSC110
> homework, but this block is set aside for MAT137H5 - PCQ3. With 34 minutes left
> in the session, now is a good time to return to math.

against the wording for actual drift:

> **Time to return to MAT137 PCQ3** — You have 34 minutes left in this study
> session for MAT137H5 F 2026 - PCQ3. You have been on YouTube for 3 minutes.

## How the nudge reaches you

`alert` is the default: a modal dialog that auto-dismisses after 45 seconds.

That is deliberate, and it is the one place this design is louder than it would
like to be. A banner posted through `osascript` is attributed to **Script
Editor**, and macOS discards it **with a success exit code** when that app's
notifications are switched off — the default state on a fresh machine. Measured
on a real one: `banner` reported delivered and was never visible. A nudge that
can vanish silently makes a broken watcher indistinguishable from a working one,
so the channel that cannot be suppressed is the default.

- `alert` — modal, cannot be suppressed, auto-dismisses. The default.
- `banner` — the polite Notification Center banner. Needs **System Settings →
  Notifications → Script Editor → Allow Notifications**, or it is silently dropped.
- `speak` — reads the nudge aloud. Unmissable and needs no permission, but off by
  default and opt-in only: it is useless in a library and startling anywhere else.
- Combine with `+`, e.g. `PROCRASTINATION_NUDGE_STYLE=alert+speak`.

Delivery is reported honestly: `/status` exposes `last_delivery_error`, and
`POST /test-nudge` returns what actually happened rather than assuming success.

## Only one watcher at a time

Two servers on the same database would double every total and nudge twice for
the same drift, which is easy to cause by leaving an old one running on another
port. A heartbeat row (`focus_watcher`) means the second process stands by,
samples nothing, and says so in `/status` as `standby_for_pid`. A lock whose
heartbeat goes quiet is taken over, so a killed server never blocks the next one.

## Run it

```sh
cd /Users/rayanalikhan/ember-hacks
PYTHONPATH=backend:. backend/.venv/bin/uvicorn procasination.server:app --port 8766
```

`server.py` wraps the backend's own lifespan rather than editing it. Running
`app.main:app` still gives you the plain calendar backend with no watcher.

```sh
cd procasination && make test     # 64 tests, no network
```

To see the whole loop without waiting, `make run-fast` nudges after 20s of drift
instead of 90s. `make watch` shows a live countdown.

### macOS permissions

Both are one-time prompts on first run:

- **Accessibility** — window titles. Without it you get app names only.
- **Automation**, per browser — the active tab's URL.
- **Screen Recording** — only if screenshot escalation is enabled.

`POST /api/procrastination/sample` returns one raw reading; an empty
`window_title` for an app that plainly has windows means Accessibility has not
been granted yet. `window_titles_readable` in `/status` reports the same thing.

## Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/procrastination/status` | live state: current activity, verdict, drift, why it is holding |
| `GET` | `/api/procrastination/sessions` | recent focus sessions with on/off-task totals |
| `GET` | `/api/procrastination/sessions/{id}` | full timeline plus every nudge sent |
| `PATCH` | `/api/procrastination/settings` | toggle the watcher and retune thresholds mid-session |
| `POST` | `/api/procrastination/sample` | one raw reading, for verifying permissions |
| `POST` | `/api/procrastination/test-nudge` | check push and native delivery |

## Configuration

All optional, and all read from `backend/.env` alongside the existing settings.

| Variable | Default | Notes |
| --- | --- | --- |
| `PROCRASTINATION_TEXT_MODEL` | `google/gemini-3.1-flash-lite` | |
| `PROCRASTINATION_VISION_MODEL` | `google/gemini-3.8-flash` | |
| `PROCRASTINATION_SAMPLE_INTERVAL_SECONDS` | `15` | how often the screen is read |
| `PROCRASTINATION_VERDICT_INTERVAL_SECONDS` | `60` | how often unresolved segments go to the model |
| `PROCRASTINATION_IDLE_THRESHOLD_SECONDS` | `600` | see below |
| `PROCRASTINATION_NUDGE_AFTER_SECONDS` | `90` | sustained drift before interrupting |
| `PROCRASTINATION_NUDGE_COOLDOWN_SECONDS` | `600` | minimum spacing between nudges |
| `PROCRASTINATION_VISION_CONFIDENCE_THRESHOLD` | `75` | below this, text alone cannot accuse |
| `PROCRASTINATION_MAX_SCREENSHOTS_PER_SESSION` | `12` | |
| `PROCRASTINATION_ENABLE_SCREENSHOTS` | `1` | `0` for a text-only watcher |
| `PROCRASTINATION_SENSITIVE_APPS` | password managers, messaging apps, mail | |
| `PROCRASTINATION_ENABLE_WEB_PUSH` | `1` | |
| `PROCRASTINATION_ENABLE_NATIVE` | `1` | macOS notification |
| `PROCRASTINATION_NUDGE_STYLE` | `alert` | `banner`, `alert`, `speak`, or e.g. `alert+speak` |

The idle threshold is deliberately generous. A student reading a proof, watching
a lecture, or working on paper produces no input for minutes at a time, so a
short threshold accuses people of slacking while they study. Ten minutes of
nothing means away from the desk.

## Tables

`focus_sessions`, `focus_observations`, `focus_nudges`, `focus_watcher`, created on the backend's
SQLite file. `event_id` is intentionally not a foreign key: the study planner
deletes and recreates study events on every recompute, and a finished focus
session should outlive that.
