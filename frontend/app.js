import { createOnboarding, normalizePlanningProfile } from "./onboarding.js";

const COURSE_COLORS = ["blue", "plum", "green", "ochre"];
const API_BASE = String(window.__EMBER_API_BASE__ || "").replace(/\/$/, "");

const state = {
  page: "today", courseId: null, tab: "All", query: "", sort: "Due",
  theme: localStorage.getItem("semester-theme") || "light",
  weekStart: startOfWeek(new Date()), onboarded: null, profile: null,
  planning: normalizePlanningProfile(JSON.parse(localStorage.getItem("ember-planning-profile") || "null")),
  courses: [], assignments: [], events: [], studySettings: null,
  sync: null, focus: null, pushSupported: "serviceWorker" in navigator && "PushManager" in window,
  pushEnabled: false, loading: true, error: ""
};

const main = document.querySelector("main");
const detailPanel = document.querySelector("#detail-panel");
const panelScrim = document.querySelector("#panel-scrim");
const searchDialog = document.querySelector("#search-dialog");
const planningDialog = document.querySelector("#planning-dialog");
const toast = document.querySelector("#toast");

function startOfWeek(value) {
  const date = new Date(value);
  date.setHours(0, 0, 0, 0);
  date.setDate(date.getDate() - ((date.getDay() + 6) % 7));
  return date;
}
function addDays(value, days) { const date = new Date(value); date.setDate(date.getDate() + days); return date; }
function sameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
function escapeHTML(value) { return String(value ?? "").replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[c]); }

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body); } catch { /* keep status */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function notify(message, error = false) {
  toast.textContent = message; toast.dataset.error = error ? "true" : "false"; toast.hidden = false;
  clearTimeout(notify.timer); notify.timer = setTimeout(() => { toast.hidden = true; }, error ? 5000 : 2600);
}
function vapidKey(value) {
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const raw = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map(character => character.charCodeAt(0)));
}
async function pushSubscription() {
  if (!state.pushSupported) return null;
  const registration = await navigator.serviceWorker.register("/sw.js");
  return registration.pushManager.getSubscription();
}
async function refreshPushState() {
  try { state.pushEnabled = Boolean(await pushSubscription()); } catch { state.pushEnabled = false; }
}
async function togglePush() {
  const existing = await pushSubscription();
  if (existing) {
    await api(`/api/notifications/subscriptions?endpoint=${encodeURIComponent(existing.endpoint)}`, { method: "DELETE" });
    await existing.unsubscribe(); state.pushEnabled = false; notify("Browser alerts disabled"); return;
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notification permission was not granted");
  const registration = await navigator.serviceWorker.ready;
  const { publicKey } = await api("/api/notifications/vapid-public-key");
  const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKey(publicKey) });
  await api("/api/notifications/subscriptions", { method: "POST", body: JSON.stringify(subscription.toJSON()) });
  state.pushEnabled = true; notify("Browser assignment alerts enabled");
}
function courseFor(id) { return state.courses.find(c => String(c.id) === String(id)) || { id: id || "personal", code: "Personal", name: "Personal", sections: [], color: "ochre" }; }
function courseColor(course) { const index = Math.max(0, state.courses.findIndex(c => String(c.id) === String(course.id))); return course.color || COURSE_COLORS[index % COURSE_COLORS.length]; }
function colorVars(course) { const color = courseColor(course); return { solid: `var(--${color})`, tint: `var(--${color}-tint)` }; }
function dateValue(value) { return value ? new Date(value) : null; }
function dateTime(value, options = {}) { const date = dateValue(value); if (!date || Number.isNaN(date.valueOf())) return "No date"; return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: options.dateOnly ? undefined : "numeric", minute: options.dateOnly ? undefined : "2-digit", ...options }).format(date); }
function timeText(value) { const date = dateValue(value); return date ? new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date) : ""; }
function minutesBetween(start, end) { return Math.max(0, Math.round((new Date(end) - new Date(start)) / 60000)); }
function duration(minutes) { if (!Number.isFinite(minutes)) return ""; if (minutes < 60) return `${minutes}m`; const h = minutes / 60; return `${Number.isInteger(h) ? h : h.toFixed(1)}h`; }
function dueState(item) { if (item.completed) return "Completed"; const due = dateValue(item.due_at); if (!due) return "Upcoming"; if (due < new Date()) return "Overdue"; if (sameDay(due, new Date())) return "Today"; return "Upcoming"; }
function dueText(item) { if (item.completed) return "Completed in Quercus"; if (!item.due_at) return "No due date"; const prefix = dueState(item); return `${prefix === "Upcoming" ? "Due" : prefix} ${dateTime(item.due_at)}`; }
function pageHeader(title, subtitle) { return `<header class="page-header"><div><h1 class="page-title">${escapeHTML(title)}</h1><p class="page-subtitle">${escapeHTML(subtitle)}</p></div></header>`; }

/* Planning constraints (v5 setup wizard). The complete profile is persisted by
   the backend; localStorage is only a fast cache for first paint. */
function persistPlanning() { localStorage.setItem("ember-planning-profile", JSON.stringify(state.planning)); }
function clearOnboardingState() {
  localStorage.removeItem("ember-planning-profile");
  localStorage.removeItem("ember-planning-dismissed");
  state.onboarded = false; state.profile = null;
  state.planning = normalizePlanningProfile(null);
  state.courses = []; state.assignments = []; state.events = []; state.courseId = null;
  if (planningDialog?.open) planningDialog.close();
}
function weekdayIndex(date) { return (new Date(date).getDay() + 6) % 7; }
function timeToMinutes(value) { const [h, m] = String(value || "").split(":").map(Number); return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : 0; }
function minutesToTime(value) { return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`; }
function planningWindowForDay(day) {
  const planWindow = day < 5 ? state.planning.windows.weekday : state.planning.windows.weekend;
  return { start: timeToMinutes(planWindow.start), end: timeToMinutes(planWindow.end) };
}
function protectedBlocksForDay(date) {
  if (!state.planning.completed) return [];
  const day = weekdayIndex(date);
  const { start, end } = planningWindowForDay(day);
  if (String(state.planning.preferences.noStudyDay) === String(day)) {
    return [{ start, end, title: "No-study day", category: "Personal", protected: true }];
  }
  const meals = state.planning.meals.filter(meal => {
    if (!meal.enabled) return false;
    if (meal.schedule === "weekdays") return day < 5;
    if (meal.schedule === "weekends") return day >= 5;
    return true;
  }).map(meal => ({ start: timeToMinutes(meal.start), end: timeToMinutes(meal.end), title: meal.label, category: "Meal", protected: true }));
  const commitments = state.planning.commitments.filter(item => item.days.includes(day))
    .map(item => ({ start: timeToMinutes(item.start), end: timeToMinutes(item.end), title: item.title, category: item.category, protected: true }));
  return [...meals, ...commitments].sort((a, b) => a.start - b.start || a.end - b.end);
}
function protectedCount() {
  if (!state.planning.completed) return 0;
  return state.planning.meals.filter(meal => meal.enabled).length
    + state.planning.commitments.length
    + (state.planning.preferences.noStudyDay !== "" ? 1 : 0);
}
function studySettingsFromPlanning(profile) {
  return { planning_profile: profile };
}

const planningSetup = createOnboarding({
  dialog: planningDialog,
  profile: state.planning,
  async onSave(profile) {
    state.planning = normalizePlanningProfile(profile);
    persistPlanning();
    localStorage.removeItem("ember-planning-dismissed");
    state.page = "planner";
    render();
    try {
      await api("/api/study/settings", { method: "PATCH", body: JSON.stringify(studySettingsFromPlanning(state.planning)) });
      const sessions = await api("/api/study/plan");
      const planned = sessions.filter(session => session.status === "active").length;
      notify(`Planning constraints saved · ${planned} study sessions planned`);
      await loadData({ quiet: true });
    } catch (error) {
      notify(`Saved locally, but the planner could not be updated: ${error.message}`, true);
    }
  },
  onDismiss() { localStorage.setItem("ember-planning-dismissed", "true"); }
});

function openPlanningSetupIfNeeded() {
  if (!state.onboarded || state.planning.completed || localStorage.getItem("ember-planning-dismissed") === "true") return;
  requestAnimationFrame(() => planningSetup.open(state.planning));
}

function protectedBlock(entry) {
  return `<div class="agenda-row is-protected"><time>${clock(entry.start)}<small>${clock(entry.end)}</small></time><i class="agenda-mark" style="--course-color:var(--ink-3)"></i><div class="agenda-copy"><strong>${escapeHTML(entry.title)}</strong><span>${escapeHTML(entry.category)} · Protected</span></div></div>`;
}
function clock(minutes) {
  const hour = Math.floor(minutes / 60); const mins = String(minutes % 60).padStart(2, "0");
  return `${hour % 12 || 12}:${mins} ${hour >= 12 ? "PM" : "AM"}`;
}

function assignmentRow(item) {
  const course = courseFor(item.course_id); const status = dueState(item);
  const points = item.points == null ? escapeHTML(item.kind || "Assignment") : `${item.points} points`;
  return `<article class="task-row" data-task="${escapeHTML(item.source_key)}"><span class="source-dot" style="--course-color:${colorVars(course).solid}" aria-hidden="true"></span><button class="task-copy text-button" data-action="details" style="padding:0;text-align:left"><div class="task-title ${item.completed ? "is-done" : ""}">${escapeHTML(item.title)}</div><div class="task-meta"><span>${escapeHTML(course.code)}</span><span class="${status === "Overdue" ? "urgent" : ""}">${escapeHTML(dueText(item))}</span><span>${points}</span></div></button></article>`;
}

function eventBlock(event) {
  const course = courseFor(event.course_id); const kind = event.source_type === "study_plan" ? "Study block" : event.kind || event.source_type;
  return `<button class="agenda-row event-button" data-event="${escapeHTML(event.id)}" data-action="event-details"><time>${escapeHTML(timeText(event.start_at))}${event.end_at ? `<small>${escapeHTML(timeText(event.end_at))}</small>` : ""}</time><i class="agenda-mark" style="--course-color:${colorVars(course).solid}"></i><span class="agenda-copy"><strong>${escapeHTML(event.title)}</strong><span>${escapeHTML(course.code)} · ${escapeHTML(kind)}</span></span></button>`;
}

function focusPanel() {
  const focus = state.focus;
  if (!focus) return `<section class="focus-strip is-warning"><div><span class="section-label">Focus watcher</span><strong>Unavailable</strong></div><p>Start the combined Ordo server to enable study-hour monitoring.</p></section>`;
  if (!focus.enabled) return `<section class="focus-strip is-muted"><div><span class="section-label">Focus watcher</span><strong>Paused</strong></div><button class="text-button" data-action="focus-enable">Enable</button></section>`;
  if (focus.watching) {
    const current = focus.current; const verdict = current?.verdict ? current.verdict.replace("_", " ") : "checking";
    return `<section class="focus-strip ${current?.verdict === "off_task" ? "is-warning" : "is-live"}"><div><span class="section-label">Focus watcher · live</span><strong>${escapeHTML(focus.assignment?.assignment_title || "Study session")}</strong></div><div class="focus-reading"><span>${escapeHTML(current?.app || "Reading screen context")}</span><strong>${escapeHTML(verdict)}</strong><small>${focus.minutes_remaining} min left${focus.off_task_streak_live_seconds ? ` · ${focus.off_task_streak_live_seconds}s drift` : ""}</small></div></section>`;
  }
  const next = focus.next_study_session;
  return `<section class="focus-strip"><div><span class="section-label">Focus watcher</span><strong>Ready for study hours</strong></div><p>${next ? `Next: ${escapeHTML(next.title)} · ${escapeHTML(dateTime(next.start_at))}` : "No upcoming study block is scheduled."}</p></section>`;
}

function renderLoading() { main.innerHTML = `<section class="page"><div class="loading-state"><span class="section-label">Ordo</span><h1>Connecting your semester</h1><p>Loading Quercus, your calendar, and the focus watcher.</p></div></section>`; }
function renderOnboarding() {
  document.querySelector("#course-nav").innerHTML = "";
  main.innerHTML = `<section class="page onboarding-page"><span class="section-label">One-time setup</span><h1 class="onboarding-title">Connect Quercus to Ordo</h1><p class="onboarding-copy">Create a Quercus access token under Account, Settings, Approved Integrations. Ordo verifies it directly with U of T and encrypts it on this Mac.</p><form id="onboarding-form" class="onboarding-form"><label><span class="field-label">Quercus API token</span><input name="token" type="password" autocomplete="off" required minlength="10" placeholder="Paste your token"></label><label><span class="field-label">Timezone</span><input name="timezone" value="America/Toronto" required></label><button class="primary-button" type="submit">Connect and sync</button>${state.error ? `<p class="form-error" role="alert">${escapeHTML(state.error)}</p>` : ""}</form><p class="privacy-note">The token never enters the browser again after setup. Screen monitoring starts only inside a generated study block.</p></section>`;
}

function renderToday() {
  const today = new Date(); const assignments = state.assignments.filter(i => !i.completed && ["Today", "Overdue"].includes(dueState(i)));
  const dayEvents = state.events.filter(e => sameDay(new Date(e.start_at), today));
  const studyMinutes = dayEvents.filter(e => e.source_type === "study_plan").reduce((sum, e) => sum + minutesBetween(e.start_at, e.end_at), 0);
  const nextEvent = state.events.find(e => new Date(e.start_at) > today);
  const guarded = protectedBlocksForDay(today);
  const dayPlan = [
    ...dayEvents.map(e => ({ sort: new Date(e.start_at).getHours() * 60 + new Date(e.start_at).getMinutes(), html: eventBlock(e) })),
    ...guarded.map(entry => ({ sort: entry.start, html: protectedBlock(entry) }))
  ].sort((a, b) => a.sort - b.sort);
  const dateLabel = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(today);
  const focusSummary = studyMinutes ? `${duration(studyMinutes)} of focus planned` : "No focus blocks planned";
  const firstName = String(state.profile?.name || "there").trim().split(/\s+/)[0];
  const dateNumber = new Intl.DateTimeFormat(undefined, { day: "2-digit" }).format(today);
  const dateMeta = new Intl.DateTimeFormat(undefined, { month: "short", weekday: "short" }).format(today).replace(",", " ·").toUpperCase();
  main.innerHTML = `<section class="page today-page"><header class="today-masthead"><div><span class="section-label">${escapeHTML(dateLabel)}</span><h1>Hello, ${escapeHTML(firstName)}.</h1><p>${escapeHTML(focusSummary)}. Here is the shape of your day.</p></div><div class="today-date" aria-hidden="true"><strong>${escapeHTML(dateNumber)}</strong><span>${escapeHTML(dateMeta)}</span></div></header>${focusPanel()}<div class="today-workspace"><section class="today-agenda"><div class="workspace-heading"><div><span class="section-label">Your day</span><h2>${nextEvent ? `Next at ${escapeHTML(timeText(nextEvent.start_at))}` : "A clear day"}</h2></div><span class="section-count">${dayPlan.length} blocks</span></div><div class="agenda-list">${dayPlan.length ? dayPlan.map(item => item.html).join("") : '<div class="calm-state"><strong>Nothing scheduled.</strong><span>The day is open for whatever needs your attention.</span></div>'}</div></section><aside class="today-attention"><div class="workspace-heading"><div><span class="section-label">Needs attention</span><h2>${assignments.length ? `${assignments.length} due now` : "Nothing urgent"}</h2></div></div>${assignments.length ? `<div class="task-list">${assignments.map(assignmentRow).join("")}</div>` : '<div class="calm-state"><strong>You are caught up.</strong><span>No due or overdue Quercus work today.</span></div>'}</aside></div></section>`;
}

function renderTasks() {
  let assignments = state.assignments.filter(item => { const group = dueState(item); if (state.tab === "Today") return ["Today", "Overdue"].includes(group) && !item.completed; if (state.tab === "Upcoming") return group === "Upcoming" && !item.completed; if (state.tab === "Completed") return item.completed; return !item.completed; }).filter(item => `${item.title} ${courseFor(item.course_id).code}`.toLowerCase().includes(state.query.toLowerCase()));
  assignments = assignments.slice().sort((a, b) => state.sort === "Course" ? courseFor(a.course_id).code.localeCompare(courseFor(b.course_id).code) : state.sort === "Points" ? (b.points || 0) - (a.points || 0) : (a.due_at || "9999").localeCompare(b.due_at || "9999"));
  const tabs = ["All", "Today", "Upcoming", "Completed"].map(tab => `<button class="tab-button ${state.tab === tab ? "is-active" : ""}" data-tab="${tab}">${tab}</button>`).join("");
  const names = state.tab === "All" ? ["Overdue", "Today", "Upcoming"] : state.tab === "Today" ? ["Overdue", "Today"] : [state.tab];
  const groups = names.map(name => ({ name, rows: assignments.filter(i => dueState(i) === name) })).filter(g => g.rows.length);
  main.innerHTML = `<section class="page tasks-page">${pageHeader("Assignments", `${state.assignments.filter(i => !i.completed).length} open · synced from Quercus`)}<div class="tab-bar">${tabs}</div><div class="task-tools"><input class="line-input" id="task-search" aria-label="Search assignments" placeholder="Search assignments" value="${escapeHTML(state.query)}"><select class="line-select" id="sort-tasks"><option${state.sort === "Due" ? " selected" : ""}>Due</option><option${state.sort === "Course" ? " selected" : ""}>Course</option><option${state.sort === "Points" ? " selected" : ""}>Points</option></select><span class="section-count">${assignments.length} shown</span></div><div class="assignment-groups">${assignments.length ? groups.map(g => `<section class="assignment-group"><div class="assignment-group-head"><h2 class="section-label ${g.name === "Overdue" ? "urgent" : ""}">${g.name}</h2><span class="section-count">${g.rows.length}</span></div><div class="task-list">${g.rows.map(assignmentRow).join("")}</div></section>`).join("") : '<div class="empty-state"><h2>No matching assignments.</h2><p>Try another tab or search.</p></div>'}</div></section>`;
}

const CALENDAR_START = 8 * 60;
const CALENDAR_END = 21 * 60;
const CALENDAR_HOUR_HEIGHT = 60;

function layoutCalendarEntries(entries) {
  const sorted = entries.slice().sort((a, b) => a.start - b.start || b.end - a.end);
  const result = [];
  let cluster = []; let clusterEnd = -1;
  const flush = () => {
    if (!cluster.length) return;
    const laneEnds = [];
    cluster.forEach(entry => {
      let lane = laneEnds.findIndex(end => end <= entry.start);
      if (lane < 0) lane = laneEnds.length;
      laneEnds[lane] = entry.end;
      entry.lane = lane;
    });
    cluster.forEach(entry => result.push({ ...entry, lanes: laneEnds.length }));
    cluster = []; clusterEnd = -1;
  };
  sorted.forEach(entry => {
    if (cluster.length && entry.start >= clusterEnd) flush();
    cluster.push(entry);
    clusterEnd = Math.max(clusterEnd, entry.end);
  });
  flush();
  return result;
}

function calendarEntry(entry) {
  const top = (entry.start - CALENDAR_START) / 60 * CALENDAR_HOUR_HEIGHT;
  const eventMinutes = entry.end - entry.start;
  const height = Math.max(14, eventMinutes / 60 * CALENDAR_HOUR_HEIGHT - 3);
  const left = entry.lane / entry.lanes * 100;
  const width = 100 / entry.lanes;
  const position = `top:${top}px;height:${height}px;left:calc(${left}% + 2px);width:calc(${width}% - 4px)`;
  const compact = eventMinutes < 40 ? " is-micro" : eventMinutes < 75 ? " is-compact" : "";
  if (entry.type === "protected") {
    return `<div class="calendar-event is-protected${compact}" style="${position}" title="${escapeHTML(entry.title)}"><strong>${escapeHTML(entry.title)}</strong><span>${clock(entry.start)}</span></div>`;
  }
  const event = entry.event; const course = courseFor(event.course_id);
  return `<button class="calendar-event${compact}" data-event="${escapeHTML(event.id)}" data-action="event-details" title="${escapeHTML(event.title)}" style="${position};background:${colorVars(course).tint};color:${colorVars(course).solid}"><strong>${escapeHTML(event.title)}</strong><span>${escapeHTML(timeText(event.start_at))}</span></button>`;
}

function renderCalendar() {
  const week = Array.from({ length: 7 }, (_, i) => addDays(state.weekStart, i));
  const heads = week.map(day => `<div class="day-head"><strong>${new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(day).toUpperCase()}</strong><span>${day.getDate()}</span></div>`).join("");
  const times = Array.from({ length: 13 }, (_, i) => `<div class="time-label">${timeText(new Date(2000, 0, 1, i + 8))}</div>`).join("");
  const columns = week.map(day => {
    const protectedEntries = protectedBlocksForDay(day).map(entry => ({
      type: "protected", title: entry.title,
      start: Math.max(CALENDAR_START, entry.start), end: Math.min(CALENDAR_END, entry.end),
    })).filter(entry => entry.end > entry.start);
    const eventEntries = state.events.filter(event => sameDay(new Date(event.start_at), day) && !event.all_day && event.source_type !== "assignment").map(event => {
      const startAt = new Date(event.start_at); const endAt = event.end_at ? new Date(event.end_at) : new Date(startAt.getTime() + 30 * 60000);
      const start = startAt.getHours() * 60 + startAt.getMinutes();
      return { type: "event", event, start: Math.max(CALENDAR_START, start), end: Math.min(CALENDAR_END, start + Math.max(15, minutesBetween(startAt, endAt))) };
    }).filter(entry => entry.start < CALENDAR_END && entry.end > CALENDAR_START && entry.end > entry.start);
    return `<div class="day-column">${layoutCalendarEntries([...protectedEntries, ...eventEntries]).map(calendarEntry).join("")}</div>`;
  }).join("");
  const deadlines = week.map(day => {
    const rows = state.events.filter(event => sameDay(new Date(event.start_at), day) && (event.all_day || event.source_type === "assignment" || (new Date(event.start_at).getHours() * 60 + new Date(event.start_at).getMinutes()) >= 1260));
    return `<div class="deadline-day">${rows.map(event => `<button data-action="event-details" data-event="${escapeHTML(event.id)}"><strong>${escapeHTML(event.title)}</strong><span>${event.all_day ? "All day" : escapeHTML(timeText(event.start_at))}</span></button>`).join("")}</div>`;
  }).join("");
  const weekEnd = addDays(state.weekStart, 6);
  main.innerHTML = `<section class="page calendar-page">${pageHeader("Calendar", "Lectures, deadlines, personal events, and generated study blocks")}<div class="calendar-shell" style="--calendar-hour:${CALENDAR_HOUR_HEIGHT}px;--calendar-height:${(CALENDAR_END - CALENDAR_START) / 60 * CALENDAR_HOUR_HEIGHT}px"><div class="calendar-toolbar"><h2>${new Intl.DateTimeFormat(undefined, { month: "long", day: "numeric" }).format(state.weekStart)} to ${new Intl.DateTimeFormat(undefined, { month: "long", day: "numeric", year: "numeric" }).format(weekEnd)}</h2><div class="button-group"><button data-action="week-prev">Previous</button><button data-action="week-today">This week</button><button data-action="week-next">Next</button></div></div><div class="deadline-grid"><span>Due</span>${deadlines}</div><div class="calendar"><div class="calendar-corner"></div>${heads}<div class="time-column">${times}</div>${columns}</div></div></section>`;
}

function renderPlanner() {
  const week = Array.from({ length: 7 }, (_, i) => addDays(state.weekStart, i)); const study = state.events.filter(e => e.source_type === "study_plan");
  const plans = week.map(day => {
    const planWindow = planningWindowForDay(weekdayIndex(day));
    const rows = [
      ...study.filter(e => sameDay(new Date(e.start_at), day)).map(e => ({
        sort: new Date(e.start_at).getHours() * 60 + new Date(e.start_at).getMinutes(),
        html: `<div class="plan-item"><span class="schedule-time">${escapeHTML(timeText(e.start_at))}</span><i class="plan-mark" style="background:${colorVars(courseFor(e.course_id)).solid}"></i><span class="plan-copy"><button class="plan-link" data-action="event-details" data-event="${escapeHTML(e.id)}"><strong>${escapeHTML(e.title)}</strong></button><small>${duration(minutesBetween(e.start_at, e.end_at))}</small></span></div>`
      })),
      ...protectedBlocksForDay(day).map(entry => ({
        sort: entry.start,
        html: `<div class="plan-item is-protected"><span class="schedule-time">${clock(entry.start).replace(":00", "")}</span><i class="plan-mark" style="background:var(--ink-3)"></i><span class="plan-copy"><strong>${escapeHTML(entry.title)}</strong><small>${escapeHTML(entry.category)} · ${duration(entry.end - entry.start)}</small></span></div>`
      }))
    ].sort((a, b) => a.sort - b.sort);
    return `<section class="day-plan"><div class="day-plan-heading"><h3>${new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(day)}</h3><span class="day-window">${clock(planWindow.start)}—${clock(planWindow.end)}</span></div><div class="day-plan-items">${rows.length ? rows.map(item => item.html).join("") : '<span class="muted">Open inside your planning window</span>'}</div></section>`;
  }).join("");
  const upcoming = state.assignments.filter(i => !i.completed && dueState(i) === "Upcoming").slice(0, 8);
  main.innerHTML = `<section class="page wide">${pageHeader("Study plan", "Generated around your lectures, events, availability, and assignment deadlines", false)}<div class="planner-actions"><button class="primary-button" data-action="recompute">Rebuild plan</button><button class="text-button" data-action="planning-setup">${state.planning.completed ? "Review constraints" : "Set up constraints"}</button><span>${study.length} sessions this week${protectedCount() ? ` · ${protectedCount()} recurring protections` : ""}</span></div><div class="planner-grid"><div class="planner-week">${plans}</div><aside class="planner-backlog"><div class="section-head"><h2 class="section-label">Upcoming work</h2><span class="section-count">${upcoming.length}</span></div><div class="unscheduled">${upcoming.map(assignmentRow).join("") || '<p class="muted">No upcoming assignments.</p>'}</div></aside></div></section>`;
}

function renderCourse() {
  const course = courseFor(state.courseId); const assignments = state.assignments.filter(i => String(i.course_id) === String(course.id));
  const meetings = state.events.filter(e => String(e.course_id) === String(course.id) && e.source_type === "course_meeting");
  const sections = (course.sections || []).map(s => s.name).filter(Boolean).join(", ") || "No section data";
  main.innerHTML = `<section class="page course-page"><div class="course-identity"><i class="course-square" style="background:${colorVars(course).solid}"></i><span>${escapeHTML(course.code)}</span></div>${pageHeader(course.name, course.term_name || "Quercus course", false)}<div class="course-summary"><section><h2 class="section-label">Enrollment</h2><dl class="definition-list"><div class="definition-row"><dt>Sections</dt><dd>${escapeHTML(sections)}</dd></div><div class="definition-row"><dt>Status</dt><dd>${escapeHTML(course.enrollment_state || "active")}</dd></div></dl></section><section><h2 class="section-label">Workload</h2><dl class="definition-list"><div class="definition-row"><dt>Open</dt><dd>${assignments.filter(i => !i.completed).length} assignments</dd></div><div class="definition-row"><dt>Meetings</dt><dd>${meetings.length} this week</dd></div></dl></section></div><section class="section"><div class="section-head"><h2 class="section-label">Course assignments</h2><span class="section-count">${assignments.length}</span></div><div class="task-list">${assignments.map(assignmentRow).join("") || '<p class="muted">No assignments synced.</p>'}</div></section></section>`;
}

function renderSettings() {
  const focus = state.focus; const settings = state.studySettings || {}; const latest = state.sync?.latest;
  const syncText = state.sync?.running ? "Syncing now" : latest ? `${latest.status} · ${dateTime(latest.finished_at || latest.started_at)}` : "Not synced yet";
  const titleReadiness = focus?.window_titles_readable === true ? "ready" : focus?.window_titles_readable === false ? "permission needed" : "not checked yet";
  main.innerHTML = `<section class="page">${pageHeader("Settings", "Quercus sync, study availability, and focus monitoring", false)}<div class="settings-list"><div class="setting-row"><div><h2>Quercus connection</h2><p>${escapeHTML(state.profile?.name || "Connected")} · ${escapeHTML(syncText)}</p></div><button class="primary-button" data-action="sync" ${state.sync?.running ? "disabled" : ""}>Sync now</button></div><div class="setting-row"><div><h2>Assignment alerts</h2><p>${state.pushSupported ? state.pushEnabled ? "Browser reminders are enabled" : "Enable browser reminders for upcoming work and study blocks" : "This browser does not support Web Push"}</p></div><button class="text-button" data-action="toggle-push" ${state.pushSupported ? "" : "disabled"}>${state.pushEnabled ? "Disable alerts" : "Enable alerts"}</button></div><div class="setting-row"><div><h2>Focus watcher</h2><p>${focus?.watching ? "Monitoring the current study block" : focus?.enabled ? "Ready; activates only during study blocks" : "Paused"}</p></div><label class="switch"><input id="focus-enabled" type="checkbox" ${focus?.enabled ? "checked" : ""}><span>Enabled</span></label></div><div class="setting-row"><div><h2>Screenshot escalation</h2><p>Used only when app and window text cannot settle a verdict.</p></div><label class="switch"><input id="screenshots-enabled" type="checkbox" ${focus?.screenshots_enabled ? "checked" : ""}><span>Enabled</span></label></div><div class="setting-row"><div><h2>Watcher readiness</h2><p>Window titles: ${titleReadiness} · OpenRouter: ${focus?.openrouter_configured ? "ready" : "not configured"}</p></div><button class="text-button" data-action="test-nudge">Test nudge</button></div><div class="setting-row"><div><h2>Planning constraints</h2><p>${state.planning.completed ? `${protectedCount()} recurring protections · ${state.planning.preferences.focusBlock}-minute focus blocks · ${state.planning.preferences.buffer} minute buffer` : "Add meals, clubs, work, commute, and the hours study should never use."}</p></div><button class="text-button" data-action="planning-setup">${state.planning.completed ? "Review setup" : "Set up"}</button></div><form id="study-settings-form" class="setting-form"><div><h2>Study hours</h2><p>The planner schedules inside these windows and the watcher follows those generated blocks.</p></div><div class="study-fields"><label><span>Weekday start</span><input name="weekday_start" type="time" value="${escapeHTML(settings.weekday_start || "09:00")}"></label><label><span>Weekday end</span><input name="weekday_end" type="time" value="${escapeHTML(settings.weekday_end || "21:30")}"></label><label><span>Weekend start</span><input name="weekend_start" type="time" value="${escapeHTML(settings.weekend_start || "10:00")}"></label><label><span>Weekend end</span><input name="weekend_end" type="time" value="${escapeHTML(settings.weekend_end || "20:00")}"></label><button class="primary-button" type="submit">Save hours</button></div></form><div class="setting-row"><div><h2>Appearance</h2><p>Use the same hierarchy in light or dark mode.</p></div><div class="segmented"><button data-theme-choice="light" class="${state.theme === "light" ? "is-active" : ""}">Light</button><button data-theme-choice="dark" class="${state.theme === "dark" ? "is-active" : ""}">Dark</button></div></div></div></section>`;
}

function renderCourseNav() {
  document.querySelector("#course-nav").innerHTML = state.courses.map(course => `<button class="course-item ${state.page === "course" && String(state.courseId) === String(course.id) ? "is-active" : ""}" data-course="${escapeHTML(course.id)}"><span class="course-name"><i class="course-square" style="background:${colorVars(course).solid}"></i><span class="course-copy"><span>${escapeHTML(course.code)}</span><small>${escapeHTML(course.name)}</small></span></span><span>${state.assignments.filter(i => String(i.course_id) === String(course.id) && !i.completed).length}</span></button>`).join("");
}

function render() {
  document.documentElement.dataset.theme = state.theme;
  if (state.loading) return renderLoading(); if (!state.onboarded) return renderOnboarding();
  document.querySelectorAll("[data-page]").forEach(button => { const active = button.dataset.page === state.page; button.classList.toggle("is-active", active); active ? button.setAttribute("aria-current", "page") : button.removeAttribute("aria-current"); });
  document.querySelector('[data-count="today"]').textContent = state.assignments.filter(i => !i.completed && ["Today", "Overdue"].includes(dueState(i))).length || "";
  document.querySelector('[data-count="tasks"]').textContent = state.assignments.filter(i => !i.completed).length || ""; renderCourseNav();
  if (state.page === "today") renderToday(); else if (state.page === "tasks") renderTasks(); else if (state.page === "calendar") renderCalendar(); else if (state.page === "planner") renderPlanner(); else if (state.page === "course") renderCourse(); else renderSettings();
}

async function loadData({ quiet = false } = {}) {
  if (!quiet) { state.loading = true; render(); }
  try {
    const onboarding = await api("/api/onboarding/status"); state.onboarded = onboarding.onboarded; state.profile = onboarding.profile || null;
    if (!state.onboarded) {
      clearOnboardingState();
      return;
    }
    const start = state.weekStart.toISOString(); const end = addDays(state.weekStart, 7).toISOString();
    const results = await Promise.allSettled([api("/api/courses"), api("/api/assignments?include_completed=true"), api(`/api/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`), api("/api/study/settings"), api("/api/sync/status"), api("/api/procrastination/status")]);
    const [courses, assignments, events, settings, sync, focus] = results;
    if (courses.status === "fulfilled") state.courses = courses.value; if (assignments.status === "fulfilled") state.assignments = assignments.value; if (events.status === "fulfilled") state.events = events.value;
    if (settings.status === "fulfilled") {
      state.studySettings = settings.value;
      if (settings.value.planning_profile?.version === 5) {
        state.planning = normalizePlanningProfile(settings.value.planning_profile);
        persistPlanning();
      } else if (state.planning.version === 5 && state.planning.completed) {
        // One-time migration for profiles created by the first V5 frontend,
        // when planning constraints lived only in localStorage.
        state.studySettings = await api("/api/study/settings", {
          method: "PATCH",
          body: JSON.stringify(studySettingsFromPlanning(state.planning)),
        });
      }
    }
    if (sync.status === "fulfilled") state.sync = sync.value;
    state.focus = focus.status === "fulfilled" ? focus.value : null;
    const failure = results.slice(0, 5).find(r => r.status === "rejected"); if (failure) throw failure.reason;
    await refreshPushState();
    if (!state.courseId && state.courses.length) state.courseId = state.courses[0].id; state.error = "";
  } catch (error) { state.error = error.message || "Could not connect to Ordo."; if (quiet) notify(state.error, true); }
  finally { state.loading = false; render(); }
}

function showDetail() { detailPanel.classList.add("is-open"); detailPanel.setAttribute("aria-hidden", "false"); detailPanel.inert = false; panelScrim.hidden = false; detailPanel.querySelector("[data-action='close-panel']")?.focus(); }
function closeDetail() { detailPanel.classList.remove("is-open"); detailPanel.setAttribute("aria-hidden", "true"); detailPanel.inert = true; panelScrim.hidden = true; }
function openAssignment(id) {
  const item = state.assignments.find(i => i.source_key === id); if (!item) return; const course = courseFor(item.course_id);
  const description = escapeHTML(String(item.description || "").replace(/<[^>]*>/g, " ")).slice(0, 1200);
  detailPanel.innerHTML = `<div class="detail-content"><div class="detail-top"><button class="icon-button" data-action="close-panel">Close</button></div><div class="course-identity"><i class="course-square" style="background:${colorVars(course).solid}"></i><span>${escapeHTML(course.code)}</span></div><h2 class="detail-title">${escapeHTML(item.title)}</h2><p class="detail-course">${escapeHTML(course.name)}</p><section class="section"><h3 class="section-label">Quercus details</h3><dl class="definition-list"><div class="definition-row"><dt>Due</dt><dd class="${dueState(item) === "Overdue" ? "urgent" : ""}">${escapeHTML(dueText(item))}</dd></div><div class="definition-row"><dt>Type</dt><dd>${escapeHTML(item.kind || "assignment")}</dd></div><div class="definition-row"><dt>Points</dt><dd>${item.points ?? "Not specified"}</dd></div><div class="definition-row"><dt>Status</dt><dd>${escapeHTML(item.submission_status || (item.completed ? "completed" : "open"))}</dd></div></dl></section>${description ? `<section class="notes"><h3 class="section-label">Description</h3><p>${description}</p></section>` : ""}</div><div class="detail-actions">${item.url ? `<a class="primary-button button-link" href="${escapeHTML(item.url)}" target="_blank" rel="noreferrer">Open in Quercus</a>` : ""}<button class="text-button" data-action="close-panel">Close</button></div>`; showDetail();
}
function openEvent(id) {
  const event = state.events.find(i => String(i.id) === String(id)); if (!event) return; const course = courseFor(event.course_id); const study = event.source_type === "study_plan";
  detailPanel.innerHTML = `<div class="detail-content"><div class="detail-top"><button class="icon-button" data-action="close-panel">Close</button></div><div class="course-identity"><i class="course-square" style="background:${colorVars(course).solid}"></i><span>${escapeHTML(course.code)}</span></div><h2 class="detail-title">${escapeHTML(event.title)}</h2><p class="detail-course">${escapeHTML(event.source_type.replaceAll("_", " "))}</p><section class="section"><h3 class="section-label">Schedule</h3><dl class="definition-list"><div class="definition-row"><dt>Starts</dt><dd>${escapeHTML(dateTime(event.start_at))}</dd></div><div class="definition-row"><dt>Ends</dt><dd>${escapeHTML(dateTime(event.end_at))}</dd></div><div class="definition-row"><dt>Location</dt><dd>${escapeHTML(event.location || "Not specified")}</dd></div><div class="definition-row"><dt>Status</dt><dd>${escapeHTML(event.status)}</dd></div></dl></section>${event.description ? `<section class="notes"><h3 class="section-label">Details</h3><p>${escapeHTML(event.description)}</p></section>` : ""}</div><div class="detail-actions">${study ? `<button class="primary-button" data-action="session-complete" data-event="${escapeHTML(event.id)}">Complete</button><button class="text-button" data-action="session-skip" data-event="${escapeHTML(event.id)}">Skip and reschedule</button>` : event.url ? `<a class="primary-button button-link" href="${escapeHTML(event.url)}" target="_blank" rel="noreferrer">Open source</a>` : ""}<button class="text-button" data-action="close-panel">Close</button></div>`; showDetail();
}

async function setSessionStatus(id, status) {
  if (!id) return;
  await api(`/api/study/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ status }) });
  closeDetail();
  notify(status === "completed" ? "Study block completed" : "Study block skipped and plan updated");
  await loadData({ quiet: true });
}

async function handleAction(action, target) {
  try {
    if (action === "details") return openAssignment(target.closest("[data-task]")?.dataset.task); if (action === "event-details") return openEvent(target.closest("[data-event]")?.dataset.event); if (action === "close-panel") return closeDetail();
    if (["week-prev", "week-next", "week-today"].includes(action)) { state.weekStart = action === "week-today" ? startOfWeek(new Date()) : addDays(state.weekStart, action === "week-prev" ? -7 : 7); closeDetail(); await loadData({ quiet: true }); return; }
    if (action === "sync") { await api("/api/sync", { method: "POST", body: "{}" }); notify("Quercus sync started"); await loadData({ quiet: true }); return; }
    if (action === "recompute") { const result = await api("/api/study/recompute", { method: "POST", body: "{}" }); notify(`${result.sessions_created} study sessions planned`); await loadData({ quiet: true }); return; }
    if (action === "focus-enable") { await api("/api/procrastination/settings", { method: "PATCH", body: JSON.stringify({ enabled: true }) }); await loadData({ quiet: true }); return; }
    if (action === "test-nudge") { const result = await api("/api/procrastination/test-nudge", { method: "POST", body: "{}" }); const delivered = result.delivered_native || result.delivered_push; notify(delivered ? "Test nudge delivered" : result.error || "No nudge channel is enabled", !delivered); return; }
    if (action === "toggle-push") { await togglePush(); render(); return; }
    if (action === "planning-setup") { planningSetup.open(state.planning); return; }
    if (["session-complete", "session-skip"].includes(action)) { await setSessionStatus(target.closest("[data-event]")?.dataset.event, action === "session-complete" ? "completed" : "skipped"); }
  } catch (error) { notify(error.message, true); }
}

document.addEventListener("click", event => {
  const page = event.target.closest("[data-page]"); if (page) { state.page = page.dataset.page; closeDetail(); render(); main.scrollTop = 0; return; }
  const course = event.target.closest("[data-course]"); if (course) { state.page = "course"; state.courseId = course.dataset.course; render(); return; }
  const tab = event.target.closest("[data-tab]"); if (tab) { state.tab = tab.dataset.tab; renderTasks(); return; }
  const theme = event.target.closest("[data-theme-choice]"); if (theme) { state.theme = theme.dataset.themeChoice; localStorage.setItem("semester-theme", state.theme); render(); return; }
  const action = event.target.closest("[data-action]"); if (action) void handleAction(action.dataset.action, action);
});
main.addEventListener("input", event => { if (event.target.id === "task-search") { state.query = event.target.value; renderTasks(); document.querySelector("#task-search")?.focus(); } });
main.addEventListener("change", event => {
  if (event.target.id === "sort-tasks") { state.sort = event.target.value; renderTasks(); }
  if (["focus-enabled", "screenshots-enabled"].includes(event.target.id)) { const body = event.target.id === "focus-enabled" ? { enabled: event.target.checked } : { enable_screenshots: event.target.checked }; void api("/api/procrastination/settings", { method: "PATCH", body: JSON.stringify(body) }).then(() => loadData({ quiet: true })).catch(e => notify(e.message, true)); }
});
main.addEventListener("submit", async event => {
  event.preventDefault(); const data = new FormData(event.target);
  try {
    if (event.target.id === "onboarding-form") {
      await api("/api/onboarding", { method: "POST", body: JSON.stringify({ quercus_api_token: data.get("token"), timezone: data.get("timezone"), reminder_offsets_days: [7, 3, 1] }) });
      notify("Connected. Initial sync is running.");
      await loadData();
      openPlanningSetupIfNeeded();
    }
    if (event.target.id === "study-settings-form") { await api("/api/study/settings", { method: "PATCH", body: JSON.stringify(Object.fromEntries(data.entries())) }); notify("Study hours saved and plan rebuilt"); await loadData({ quiet: true }); }
  } catch (error) { state.error = error.message; notify(error.message, true); render(); }
});

document.querySelectorAll(".search-trigger").forEach(button => button.addEventListener("click", () => { searchDialog.showModal(); document.querySelector("#global-search").focus(); renderSearch(""); }));
document.querySelector("#global-search").addEventListener("input", event => renderSearch(event.target.value));
function renderSearch(query) {
  const term = query.trim().toLowerCase(); const pages = ["Today", "Tasks", "Calendar", "Planner", "Settings"].filter(i => !term || i.toLowerCase().includes(term)).map(i => ({ title: i, type: "Page", page: i.toLowerCase() }));
  const courses = state.courses.filter(c => !term || `${c.code} ${c.name}`.toLowerCase().includes(term)).map(c => ({ title: `${c.code} · ${c.name}`, type: "Course", course: c.id }));
  const tasks = state.assignments.filter(i => term && i.title.toLowerCase().includes(term)).map(i => ({ title: i.title, type: "Assignment", task: i.source_key })); const results = [...tasks, ...courses, ...pages].slice(0, 10);
  document.querySelector("#search-results").innerHTML = results.length ? results.map(r => `<button class="search-result" type="button" data-search-page="${r.page || ""}" data-search-course="${r.course || ""}" data-search-task="${r.task || ""}"><span>${escapeHTML(r.title)}</span><span>${r.type}</span></button>`).join("") : '<p class="muted" style="padding:14px 22px">Nothing matches that search.</p>';
}
document.querySelector("#search-results").addEventListener("click", event => { const result = event.target.closest(".search-result"); if (!result) return; searchDialog.close(); if (result.dataset.searchTask) openAssignment(result.dataset.searchTask); else if (result.dataset.searchCourse) { state.page = "course"; state.courseId = result.dataset.searchCourse; render(); } else { state.page = result.dataset.searchPage; render(); } });

window.semesterSubmitNaturalLanguage = async text => {
  const result = await api("/api/input", { method: "POST", body: JSON.stringify({ text }) });
  if (result.needs_clarification) return { ok: false, changed: false, message: result.question || "I need one more detail." };
  const created = result.created_events?.length || 0;
  const deleted = result.deleted_events?.length || 0;
  const changed = created + deleted > 0;
  if (changed) await loadData({ quiet: true });
  const fallback = [
    created ? `${created} event${created === 1 ? "" : "s"} created` : "",
    deleted ? `${deleted} event${deleted === 1 ? "" : "s"} deleted` : "",
  ].filter(Boolean).join(" · ") || "No calendar changes were needed.";
  return { ok: true, changed, message: result.reply && changed ? `${result.reply} · ${fallback}` : result.reply || fallback };
};
panelScrim.addEventListener("click", closeDetail);
document.addEventListener("keydown", event => { const modifier = event.metaKey || event.ctrlKey; if (modifier && event.key.toLowerCase() === "k") { event.preventDefault(); searchDialog.showModal(); document.querySelector("#global-search").focus(); renderSearch(""); } if (modifier && event.key.toLowerCase() === "j" && state.onboarded) { event.preventDefault(); window.semesterOpenVoiceCommand?.(); } if (event.key === "Escape") closeDetail(); });
setInterval(async () => {
  if (document.hidden) return;
  try {
    const onboarding = await api("/api/onboarding/status");
    if (!onboarding.onboarded) { clearOnboardingState(); render(); return; }
    if (!state.onboarded) { await loadData(); return; }
    state.focus = await api("/api/procrastination/status"); state.sync = await api("/api/sync/status");
    if (["today", "settings"].includes(state.page)) render();
  } catch { /* keep last known state */ }
}, 15000);

render();
void loadData().then(openPlanningSetupIfNeeded);
