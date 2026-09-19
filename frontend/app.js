const courses = [
  { id: "csc110", code: "CSC110Y5", name: "Foundations of Computer Science I", professor: "Andrew Petersen", meets: "Mon / Wed / Fri · 9:00 to 11:00", color: "blue", note: "The course notes order matters more than any video series. Type every example and run it." },
  { id: "mat137", code: "MAT137H5", name: "Differential Calculus", professor: "Nadya Askaripour", meets: "Mon / Wed · 16:00 to 17:00", color: "plum", note: "Work every practice problem by hand, one step per line, and write the reason beside any non-obvious step." },
  { id: "isp100", code: "ISP100H5", name: "Writing for University", professor: "Ryan Shuvera", meets: "Tue · 15:00 to 18:00", color: "green", note: "Use one idea per paragraph. Leave three minutes at the end for spelling and structure." },
  { id: "isp130", code: "ISP130H5", name: "Numeracy for University", professor: "Rita Karrass", meets: "Thu · 9:00 to 12:00", color: "ochre", note: "Quantifiers, truth tables, and validity overlap with MAT137 and CSC110 this week." }
];

const seedTasks = [
  { id: 1, course: "isp100", title: "Writing Story rough draft", due: "today", estimate: 90, priority: "High", scheduled: "10:00", note: "Two pages, double spaced. Write the version you would be willing to read aloud, then cut the first paragraph." },
  { id: 2, course: "csc110", title: "Read CSC110 notes, 3.1 to 3.5", due: "today", estimate: 90, priority: "Medium", scheduled: "13:00", note: "Focus on the Function Design Recipe, docstrings, and doctests." },
  { id: 3, course: "isp130", title: "Read Levin 5.2, page 307", due: "today", estimate: 45, priority: "Medium", scheduled: "15:00", note: "Quantifiers and truth tables overlap with MAT137 videos 1.3 to 1.6." },
  { id: 4, course: "mat137", title: "MAT137 Pre-Class Quiz 3", due: "today", dueTime: "11:59 PM", estimate: 30, priority: "High", scheduled: "", note: "Confirm the submission registered before the deadline." },
  { id: 5, course: "isp100", title: "Book an RGASC writing appointment", due: "overdue", dueTime: "Friday", estimate: 10, priority: "High", scheduled: "", note: "Book support for the Writing Story draft." },
  { id: 6, course: "mat137", title: "Term Test 1 practice set", due: "Monday", estimate: 120, priority: "Medium", scheduled: "", note: "Start now so the final week is review rather than first contact." },
  { id: 7, course: "csc110", title: "Pre-Lecture Reading Check 3", due: "Monday", dueTime: "9:00 AM", estimate: 30, priority: "High", scheduled: "Monday 8:00", note: "Unlimited attempts before the deadline, zero after." },
  { id: 8, course: "isp130", title: "Discussion 1 post", due: "Thursday", estimate: 45, priority: "Low", scheduled: "", note: "Reply to two classmates after posting." },
  { id: 9, course: "csc110", title: "Install Python 3.11 and PyCharm", due: "done", estimate: 60, priority: "Medium", scheduled: "", done: true, note: "Use a virtual environment before installing python-ta on macOS." }
];

const events = [
  { day: 0, start: 540, end: 660, title: "CSC110 lecture", course: "csc110" },
  { day: 0, start: 660, end: 720, title: "MAT137 tutorial", course: "mat137" },
  { day: 0, start: 960, end: 1020, title: "MAT137 lecture", course: "mat137" },
  { day: 1, start: 900, end: 1080, title: "ISP100 seminar", course: "isp100" },
  { day: 2, start: 540, end: 660, title: "CSC110 lecture", course: "csc110" },
  { day: 2, start: 960, end: 1020, title: "MAT137 lecture", course: "mat137" },
  { day: 3, start: 540, end: 720, title: "ISP130 class", course: "isp130" },
  { day: 4, start: 540, end: 660, title: "CSC110 lecture", course: "csc110" },
  { day: 5, start: 600, end: 690, title: "Writing Story draft", course: "isp100", task: 1 },
  { day: 5, start: 780, end: 870, title: "CSC110 notes", course: "csc110", task: 2 },
  { day: 5, start: 900, end: 945, title: "Levin 5.2", course: "isp130", task: 3 }
];

const state = {
  page: "today",
  courseId: "mat137",
  tab: "All",
  query: "",
  sort: "Due",
  theme: localStorage.getItem("semester-theme") || "light",
  tasks: JSON.parse(localStorage.getItem("semester-tasks") || "null") || structuredClone(seedTasks)
};

const main = document.querySelector("main");
const detailPanel = document.querySelector("#detail-panel");
const panelScrim = document.querySelector("#panel-scrim");
const quickDialog = document.querySelector("#quick-add");
const searchDialog = document.querySelector("#search-dialog");
const toast = document.querySelector("#toast");

function courseFor(id) { return courses.find(course => course.id === id); }
function openTasks() { return state.tasks.filter(task => !task.done); }
function duration(minutes) {
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `${hours}h${remainder ? ` ${remainder}m` : ""}`;
}
function dueText(task) { return task.due === "overdue" ? `Overdue${task.dueTime ? `, ${task.dueTime}` : ""}` : task.due === "today" ? `Today${task.dueTime ? `, ${task.dueTime}` : ""}` : task.done ? "Completed" : `${task.due}${task.dueTime ? `, ${task.dueTime}` : ""}`; }
function persist() { localStorage.setItem("semester-tasks", JSON.stringify(state.tasks)); }
function colorVars(course) { return { solid: `var(--${course.color})`, tint: `var(--${course.color}-tint)` }; }
function escapeHTML(value) { return String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]); }

function notify(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => { toast.hidden = true; }, 2200);
}

function row(task, options = {}) {
  const course = courseFor(task.course);
  const meta = [
    `<span><i class="course-mark" style="background:${colorVars(course).solid}"></i>${course.code}</span>`,
    `<span class="${task.due === "overdue" ? "urgent" : ""}">${dueText(task)}</span>`,
    `<span class="${task.priority === "High" ? "urgent" : ""}">${task.priority}</span>`,
    `<span>${duration(task.estimate)}</span>`
  ].join("");
  return `<article class="task-row" data-task="${task.id}">
    <button class="check ${task.done ? "is-done" : ""}" data-action="toggle" aria-label="${task.done ? "Mark not complete" : "Mark complete"}">${task.done ? "✓" : ""}</button>
    <button class="task-copy text-button" data-action="details" style="padding:0;text-align:left">
      <div class="task-title ${task.done ? "is-done" : ""}">${escapeHTML(task.title)}</div>
      <div class="task-meta">${meta}</div>
    </button>
    ${options.actions === false ? "" : `<div class="row-actions"><button data-action="schedule">Schedule</button><button data-action="details">Edit</button><button data-action="remove" aria-label="Remove ${escapeHTML(task.title)}">Remove</button></div>`}
  </article>`;
}

function pageHeader(title, subtitle, action = true) {
  return `<header class="page-header"><div><h1 class="page-title">${title}</h1><p class="page-subtitle">${subtitle}</p></div>${action ? '<button class="primary-button" data-action="quick-add">New task</button>' : ""}</header>`;
}

function renderToday() {
  const today = openTasks().filter(task => task.due === "today");
  const overdue = openTasks().filter(task => task.due === "overdue");
  const planned = today.filter(task => task.scheduled).reduce((total, task) => total + task.estimate, 0);
  const blocks = events.filter(event => event.day === 5);
  const focusTask = state.tasks.find(task => task.id === 2 && !task.done) || today[0];
  const priorities = [...overdue, ...today.filter(task => task.priority === "High")].slice(0, 3);
  const loose = [...overdue, ...today.filter(task => !task.scheduled)];
  const dayCapacity = planned + 135;
  const planPercent = dayCapacity ? Math.round(planned / dayCapacity * 100) : 0;
  const gaps = {
    1: { time: "11:30 AM", label: "Open focus window", length: "1h 30m" },
    2: { time: "2:30 PM", label: "Reset and walk", length: "30m" }
  };
  const agenda = blocks.map((event, index) => {
    const course = courseFor(event.course);
    const linkedTask = state.tasks.find(task => task.id === event.task);
    const isNow = event.task === 2;
    const block = `<button class="agenda-block ${isNow ? "is-now" : ""}" data-task="${event.task || ""}" data-action="${event.task ? "details" : "calendar-note"}" style="--block-color:${colorVars(course).solid};--block-tint:${colorVars(course).tint}">
      <time class="agenda-time">${clock(event.start)}</time>
      <span class="agenda-copy"><strong>${escapeHTML(event.title)}</strong><span>${course.code}${isNow ? " · In focus" : linkedTask?.priority === "High" ? " · High priority" : ""}</span></span>
      <span class="agenda-duration">${duration(event.end - event.start)}</span>
    </button>`;
    const gap = gaps[index + 1];
    return `${block}${gap ? `<button class="agenda-gap" data-action="quick-add"><time class="agenda-time">${gap.time}</time><span>${gap.label} · ${gap.length}</span><span>Plan work</span></button>` : ""}`;
  }).join("");
  main.innerHTML = `<section class="page today-page">
    ${pageHeader("Today", "Saturday, September 19 · A focused study day")}
    <div class="day-ledger" aria-label="Day summary"><div class="ledger-item"><span>Open today</span><strong>${today.length + overdue.length} tasks</strong></div><div class="ledger-item"><span>Planned focus</span><strong>${duration(planned)}</strong></div><div class="ledger-item"><span>Next deadline</span><strong>Quiz · 11:59 PM</strong></div></div>
    <div class="today-grid">
      <div class="today-main">
        ${focusTask ? `<section class="focus-strip" aria-label="Current focus"><div class="focus-kicker"><i class="live-dot"></i>Right now</div><h2>${escapeHTML(focusTask.title)}</h2><div class="focus-meta"><span>${courseFor(focusTask.course).code}</span><span>${duration(focusTask.estimate)} focus block</span></div><button class="focus-action" data-task="${focusTask.id}" data-action="details">Open task</button></section>` : ""}
        <section class="agenda"><div class="agenda-head"><h2 class="section-label">Your day</h2><span class="section-count">${duration(planned)} planned</span></div><div class="agenda-line">${agenda}</div></section>
        <section class="section"><div class="section-head"><h2 class="section-label">Loose tasks</h2><span class="section-count">${loose.length}</span></div>${loose.length ? `<div class="task-list">${loose.map(task => row(task)).join("")}</div>` : `<div class="empty-state"><h2>Everything has a place.</h2><p>Your open work is already scheduled into the day.</p></div>`}</section>
      </div>
      <aside class="today-rail" aria-label="Day overview">
        <section class="rail-section"><div class="rail-head"><h2 class="section-label">Day progress</h2><span class="section-count">${planPercent}%</span></div><div class="day-progress"><div class="progress-number"><strong>${planPercent}%</strong><span>planned</span></div><div class="progress-track"><i style="width:${planPercent}%"></i></div><p class="progress-caption">${duration(planned)} placed · 2h 15m still open</p></div></section>
        <section class="rail-section"><div class="rail-head"><h2 class="section-label">Priorities</h2><span class="section-count">${priorities.length}</span></div><div class="priority-list">${priorities.map(task => { const course = courseFor(task.course); return `<button class="priority-item" data-task="${task.id}" data-action="details" style="--course-color:${colorVars(course).solid}"><i></i><span><strong>${escapeHTML(task.title)}</strong><span class="${task.due === "overdue" ? "urgent" : ""}">${dueText(task)} · ${duration(task.estimate)}</span></span></button>`; }).join("")}</div></section>
        <section class="rail-section"><div class="rail-head"><h2 class="section-label">Monday</h2><span class="section-count">Next</span></div><div class="priority-list"><div class="priority-item" style="--course-color:var(--blue)"><i></i><span><strong>CSC110 lecture</strong><span>9:00 to 11:00 AM</span></span></div><div class="priority-item" style="--course-color:var(--plum)"><i></i><span><strong>MAT137 tutorial</strong><span>11:00 AM to noon</span></span></div></div></section>
      </aside>
    </div>
  </section>`;
}

function renderTasks() {
  let tasks = state.tasks.filter(task => {
    if (state.tab === "Today") return !task.done && ["today", "overdue"].includes(task.due);
    if (state.tab === "Upcoming") return !task.done && !["today", "overdue"].includes(task.due);
    if (state.tab === "Completed") return task.done;
    return !task.done;
  }).filter(task => `${task.title} ${courseFor(task.course).code}`.toLowerCase().includes(state.query.toLowerCase()));
  const dueOrder = { overdue: 0, today: 1, Monday: 2, Thursday: 3, done: 4 };
  const priorityOrder = { High: 0, Medium: 1, Low: 2 };
  tasks = tasks.slice().sort((a, b) => state.sort === "Priority" ? priorityOrder[a.priority] - priorityOrder[b.priority] : state.sort === "Estimate" ? b.estimate - a.estimate : dueOrder[a.due] - dueOrder[b.due]);
  const tabs = ["All", "Today", "Upcoming", "Completed"].map(tab => `<button class="tab-button ${state.tab === tab ? "is-active" : ""}" data-tab="${tab}">${tab}</button>`).join("");
  const groupFor = task => task.done ? "Completed" : task.due === "overdue" ? "Overdue" : task.due === "today" ? "Today" : "Upcoming";
  const groupNames = state.tab === "All" ? ["Overdue", "Today", "Upcoming"] : [state.tab];
  const groups = groupNames.map(name => ({ name, tasks: tasks.filter(task => groupFor(task) === name) })).filter(group => group.tasks.length);
  main.innerHTML = `<section class="page">
    ${pageHeader("Tasks", `${openTasks().length} open · ${state.tasks.filter(task => task.done).length} completed`)}
    <div class="tab-bar" role="tablist">${tabs}</div>
    <div class="task-tools"><input class="line-input" id="task-search" aria-label="Search tasks" placeholder="Search tasks" value="${state.query}"><select class="line-select" id="sort-tasks" aria-label="Sort tasks"><option${state.sort === "Due" ? " selected" : ""}>Due</option><option${state.sort === "Priority" ? " selected" : ""}>Priority</option><option${state.sort === "Estimate" ? " selected" : ""}>Estimate</option></select><span class="section-count">${tasks.length} shown</span></div>
    <section class="section">${tasks.length ? groups.map(group => `<div class="task-group"><div class="section-head"><h2 class="section-label ${group.name === "Overdue" ? "urgent" : ""}">${group.name}</h2><span class="section-count">${group.tasks.length}</span></div><div class="task-list">${group.tasks.map(task => row(task)).join("")}</div></div>`).join("") : `<div class="empty-state"><h2>No matching tasks.</h2><p>Clear the search or add work to this view.</p><button class="primary-button" data-action="quick-add">Add a task</button></div>`}</section>
  </section>`;
}

function clock(minutes) {
  const hour = Math.floor(minutes / 60);
  const mins = String(minutes % 60).padStart(2, "0");
  return `${hour % 12 || 12}:${mins} ${hour >= 12 ? "PM" : "AM"}`;
}

function renderCalendar() {
  const names = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  const dates = [14, 15, 16, 17, 18, 19, 20];
  const heads = names.map((name, index) => `<div class="day-head"><strong>${name}</strong><span>${dates[index]}</span></div>`).join("");
  const times = Array.from({ length: 13 }, (_, index) => `<div class="time-label">${index + 8 > 12 ? index - 4 : index + 8}${index + 8 >= 12 ? "p" : "a"}</div>`).join("");
  const columns = names.map((_, day) => `<div class="day-column">${events.filter(event => event.day === day).map(event => {
    const course = courseFor(event.course);
    const top = (event.start - 480) / 60 * 44;
    const height = Math.max(24, (event.end - event.start) / 60 * 44 - 2);
    return `<button class="calendar-event" data-task="${event.task || ""}" style="top:${top}px;height:${height}px;background:${colorVars(course).tint};color:${colorVars(course).solid};border-top:0;border-right:0;border-bottom:0;text-align:left"><strong>${event.title}</strong><span>${clock(event.start)}</span></button>`;
  }).join("")}</div>`).join("");
  main.innerHTML = `<section class="page wide">
    ${pageHeader("Calendar", "Week of September 14 to 20", false)}
    <div class="calendar-shell"><div class="calendar-toolbar"><h2>September 2026</h2><div class="button-group"><button data-action="calendar-note">Previous</button><button data-action="calendar-note">This week</button><button data-action="calendar-note">Next</button></div></div><div class="calendar"><div class="calendar-corner"></div>${heads}<div class="time-column">${times}</div>${columns}<div class="now-line" aria-label="Current time"></div></div></div>
  </section>`;
}

function renderPlanner() {
  const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const plans = days.map((day, index) => {
    const dayEvents = events.filter(event => event.day === index);
    return `<section class="day-plan"><h3>${day.slice(0, 3)}</h3><div>${dayEvents.length ? dayEvents.map(event => `<div class="plan-item"><span class="schedule-time">${clock(event.start).replace(":00", "")}</span><span>${event.title}</span></div>`).join("") : '<span class="muted">Open</span>'}</div></section>`;
  }).join("");
  const unscheduled = openTasks().filter(task => !task.scheduled);
  main.innerHTML = `<section class="page wide">
    ${pageHeader("Planner", "Place unscheduled work beside the week that has to hold it")}
    <div class="planner-grid"><div>${plans}</div><aside><div class="section-head"><h2 class="section-label">Unscheduled</h2><span class="section-count">${unscheduled.length}</span></div><div class="unscheduled">${unscheduled.map(task => { const course = courseFor(task.course); return `<button class="task-row" data-task="${task.id}" data-action="details" style="width:100%;border-right:0;border-bottom:0;border-left:0;background:transparent;text-align:left"><i class="course-square" style="background:${colorVars(course).solid}"></i><span><strong class="task-title">${escapeHTML(task.title)}</strong><span class="task-meta"><span>${duration(task.estimate)}</span><span>${course.code}</span></span></span></button>`; }).join("")}</div></aside></div>
  </section>`;
}

function renderProgress() {
  const done = state.tasks.filter(task => task.done).length;
  const total = state.tasks.length;
  const scheduledMinutes = openTasks().filter(task => task.scheduled).reduce((sum, task) => sum + task.estimate, 0);
  const completion = total ? Math.round(done / total * 100) : 0;
  const courseRows = courses.map(course => {
    const tasks = state.tasks.filter(task => task.course === course.id);
    const completed = tasks.filter(task => task.done).length;
    const percent = tasks.length ? Math.round(completed / tasks.length * 100) : 0;
    return `<div class="course-progress-row"><div><strong>${course.code}</strong><span>${course.name}</span></div><div class="progress-track"><i style="width:${percent}%;background:${colorVars(course).solid}"></i></div><b>${completed}/${tasks.length}</b></div>`;
  }).join("");
  main.innerHTML = `<section class="page">
    ${pageHeader("Progress", "A quiet read on workload, not another score to chase", false)}
    <div class="progress-overview"><div class="metric"><span>Tasks completed</span><strong>${done}</strong><small>${completion}% of this workspace</small></div><div class="metric"><span>Focus scheduled</span><strong>${duration(scheduledMinutes)}</strong><small>Across open work</small></div><div class="metric"><span>Open workload</span><strong>${duration(openTasks().reduce((sum, task) => sum + task.estimate, 0))}</strong><small>${openTasks().length} tasks remaining</small></div></div>
    <section class="section"><div class="section-head"><h2 class="section-label">By course</h2><span class="section-count">Completed / total</span></div><div class="course-progress">${courseRows}</div></section>
    <section class="notes"><h2 class="section-label">This week</h2><p>Your schedule is weighted toward Saturday. Move one MAT137 practice block into Tuesday to keep the test review from becoming a single long session.</p></section>
  </section>`;
}

function renderCourse() {
  const course = courseFor(state.courseId);
  const tasks = state.tasks.filter(task => task.course === course.id);
  main.innerHTML = `<section class="page course-page">
    <div class="course-identity"><i class="course-square" style="background:${colorVars(course).solid}"></i><span>${course.code}</span></div>
    ${pageHeader(course.name, `${course.professor} · ${course.meets}`)}
    <div class="course-summary"><section><h2 class="section-label">Course details</h2><dl class="definition-list"><div class="definition-row"><dt>Professor</dt><dd>${course.professor}</dd></div><div class="definition-row"><dt>Meetings</dt><dd>${course.meets}</dd></div><div class="definition-row"><dt>Open work</dt><dd>${tasks.filter(task => !task.done).length} tasks</dd></div></dl></section><section><h2 class="section-label">Workload</h2><dl class="definition-list"><div class="definition-row"><dt>Estimated</dt><dd>${duration(tasks.filter(task => !task.done).reduce((sum, task) => sum + task.estimate, 0))}</dd></div><div class="definition-row"><dt>Scheduled</dt><dd>${tasks.filter(task => task.scheduled && !task.done).length} tasks</dd></div></dl></section></div>
    <section class="notes"><h2 class="section-label">Working note</h2><p>${course.note}</p></section>
    <section class="section"><div class="section-head"><h2 class="section-label">Course tasks</h2><span class="section-count">${tasks.length}</span></div><div class="task-list">${tasks.map(task => row(task)).join("")}</div></section>
  </section>`;
}

function renderSettings() {
  main.innerHTML = `<section class="page">
    ${pageHeader("Settings", "Keep the workspace readable in the conditions you study in", false)}
    <div class="settings-list"><div class="setting-row"><div><h2>Appearance</h2><p>Light follows the white paper ground. Dark keeps the same hierarchy.</p></div><div class="segmented" role="group" aria-label="Appearance"><button data-theme-choice="light" class="${state.theme === "light" ? "is-active" : ""}">Light</button><button data-theme-choice="dark" class="${state.theme === "dark" ? "is-active" : ""}">Dark</button></div></div><div class="setting-row"><div><h2>Local task data</h2><p>Changes are stored in this browser.</p></div><button class="text-button" data-action="reset">Reset demo tasks</button></div><div class="setting-row"><div><h2>Keyboard shortcuts</h2><p>Search with ⌘K, add a task with ⌘N, and close overlays with Esc.</p></div></div></div>
  </section>`;
}

function render() {
  document.documentElement.dataset.theme = state.theme;
  document.querySelectorAll("[data-page]").forEach(button => {
    const active = button.dataset.page === state.page;
    button.classList.toggle("is-active", active);
    if (button.closest(".primary-nav")) active ? button.setAttribute("aria-current", "page") : button.removeAttribute("aria-current");
  });
  document.querySelector('[data-count="today"]').textContent = openTasks().filter(task => ["today", "overdue"].includes(task.due)).length;
  document.querySelector('[data-count="tasks"]').textContent = openTasks().length;
  renderCourseNav();
  if (state.page === "today") renderToday();
  if (state.page === "tasks") renderTasks();
  if (state.page === "calendar") renderCalendar();
  if (state.page === "planner") renderPlanner();
  if (state.page === "progress") renderProgress();
  if (state.page === "course") renderCourse();
  if (state.page === "settings") renderSettings();
}

function renderCourseNav() {
  document.querySelector("#course-nav").innerHTML = courses.map(course => `<button class="course-item ${state.page === "course" && state.courseId === course.id ? "is-active" : ""}" data-course="${course.id}"><span class="course-name"><i class="course-square" style="background:${colorVars(course).solid}"></i><span class="course-copy"><span>${course.code}</span><small>${course.name}</small></span></span><span>${openTasks().filter(task => task.course === course.id).length}</span></button>`).join("");
  document.querySelector("#task-course").innerHTML = courses.map(course => `<option value="${course.id}">${course.code}</option>`).join("");
}

function openDetail(id) {
  const task = state.tasks.find(item => item.id === Number(id));
  if (!task) return;
  const course = courseFor(task.course);
  detailPanel.innerHTML = `<div class="detail-content"><div class="detail-top"><button class="icon-button" data-action="close-panel">Close</button></div><div class="course-identity"><i class="course-square" style="background:${colorVars(course).solid}"></i><span>${course.code}</span></div><h2 class="detail-title">${escapeHTML(task.title)}</h2><p class="detail-course">${course.name}</p><section class="section"><h3 class="section-label">Details</h3><dl class="definition-list"><div class="definition-row"><dt>Due</dt><dd class="${task.due === "overdue" ? "urgent" : ""}">${dueText(task)}</dd></div><div class="definition-row"><dt>Priority</dt><dd class="${task.priority === "High" ? "urgent" : ""}">${task.priority}</dd></div><div class="definition-row"><dt>Estimate</dt><dd>${duration(task.estimate)}</dd></div><div class="definition-row"><dt>Scheduled</dt><dd>${task.scheduled || "Not scheduled"}</dd></div></dl></section>${task.note ? `<section class="notes"><h3 class="section-label">Note</h3><p>${task.note}</p></section>` : ""}</div><div class="detail-actions"><button class="primary-button" data-task="${task.id}" data-action="toggle">${task.done ? "Mark not complete" : "Mark complete"}</button><button class="text-button" data-task="${task.id}" data-action="schedule">Schedule</button></div>`;
  detailPanel.classList.add("is-open");
  detailPanel.setAttribute("aria-hidden", "false");
  detailPanel.inert = false;
  panelScrim.hidden = false;
  detailPanel.querySelector("[data-action='close-panel']").focus();
}

function closeDetail() {
  detailPanel.classList.remove("is-open");
  detailPanel.setAttribute("aria-hidden", "true");
  detailPanel.inert = true;
  panelScrim.hidden = true;
}

function handleAction(action, id) {
  const task = state.tasks.find(item => item.id === Number(id));
  if (action === "quick-add") { quickDialog.showModal(); document.querySelector("#task-title").focus(); }
  if (action === "close-quick") quickDialog.close();
  if (action === "details" && task) openDetail(id);
  if (action === "close-panel") closeDetail();
  if (action === "toggle" && task) { task.done = !task.done; task.due = task.done ? "done" : "today"; persist(); closeDetail(); render(); notify(task.done ? "Task completed" : "Task reopened"); }
  if (action === "schedule" && task) { task.scheduled = task.scheduled ? "" : "Today 16:00"; persist(); closeDetail(); render(); notify(task.scheduled ? "Task scheduled for 4:00 PM" : "Task removed from schedule"); }
  if (action === "remove" && task) { state.tasks = state.tasks.filter(item => item.id !== task.id); persist(); render(); notify("Task removed"); }
  if (action === "calendar-note") notify("Calendar remains on the demo week");
  if (action === "reset") { state.tasks = structuredClone(seedTasks); persist(); render(); notify("Demo tasks reset"); }
}

document.addEventListener("click", event => {
  const pageButton = event.target.closest("[data-page]");
  if (pageButton) { state.page = pageButton.dataset.page; closeDetail(); render(); main.scrollTop = 0; return; }
  const courseButton = event.target.closest("[data-course]");
  if (courseButton) { state.page = "course"; state.courseId = courseButton.dataset.course; render(); return; }
  const tab = event.target.closest("[data-tab]");
  if (tab) { state.tab = tab.dataset.tab; renderTasks(); return; }
  const theme = event.target.closest("[data-theme-choice]");
  if (theme) { state.theme = theme.dataset.themeChoice; localStorage.setItem("semester-theme", state.theme); render(); return; }
  const actionTarget = event.target.closest("[data-action]");
  if (actionTarget) handleAction(actionTarget.dataset.action, actionTarget.closest("[data-task]")?.dataset.task);
  const calendarEvent = event.target.closest(".calendar-event[data-task]");
  if (calendarEvent?.dataset.task) openDetail(calendarEvent.dataset.task);
});

main.addEventListener("input", event => {
  if (event.target.id === "task-search") { state.query = event.target.value; renderTasks(); document.querySelector("#task-search").focus(); }
});

main.addEventListener("change", event => {
  if (event.target.id === "sort-tasks") { state.sort = event.target.value; renderTasks(); }
});

document.querySelectorAll(".search-trigger").forEach(button => button.addEventListener("click", () => { searchDialog.showModal(); document.querySelector("#global-search").focus(); renderSearch(""); }));
document.querySelector("#global-search").addEventListener("input", event => renderSearch(event.target.value));

function renderSearch(query) {
  const term = query.trim().toLowerCase();
  const pageResults = ["Today", "Tasks", "Calendar", "Planner", "Progress", "Settings"].filter(item => !term || item.toLowerCase().includes(term)).map(item => ({ title: item, type: "Page", page: item.toLowerCase() }));
  const courseResults = courses.filter(course => !term || `${course.code} ${course.name}`.toLowerCase().includes(term)).map(course => ({ title: `${course.code} · ${course.name}`, type: "Course", course: course.id }));
  const taskResults = state.tasks.filter(task => term && task.title.toLowerCase().includes(term)).map(task => ({ title: task.title, type: "Task", task: task.id }));
  const results = [...taskResults, ...courseResults, ...pageResults].slice(0, 8);
  document.querySelector("#search-results").innerHTML = results.length ? results.map(result => `<button class="search-result" type="button" data-search-page="${result.page || ""}" data-search-course="${result.course || ""}" data-search-task="${result.task || ""}"><span>${escapeHTML(result.title)}</span><span>${result.type}</span></button>`).join("") : '<p class="muted" style="padding:14px 22px">Nothing matches that search.</p>';
}

document.querySelector("#search-results").addEventListener("click", event => {
  const result = event.target.closest(".search-result");
  if (!result) return;
  searchDialog.close();
  if (result.dataset.searchTask) openDetail(result.dataset.searchTask);
  else if (result.dataset.searchCourse) { state.page = "course"; state.courseId = result.dataset.searchCourse; render(); }
  else { state.page = result.dataset.searchPage; render(); }
});

document.querySelector("#quick-form").addEventListener("submit", event => {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  state.tasks.push({ id: Date.now(), title: data.get("title").trim(), course: data.get("course"), due: data.get("due"), priority: data.get("priority"), estimate: Number(data.get("estimate")), scheduled: "", note: "" });
  persist();
  quickDialog.close();
  event.currentTarget.reset();
  state.page = "today";
  render();
  notify("Task created");
});

panelScrim.addEventListener("click", closeDetail);

document.addEventListener("keydown", event => {
  const modifier = event.metaKey || event.ctrlKey;
  if (modifier && event.key.toLowerCase() === "k") { event.preventDefault(); searchDialog.showModal(); document.querySelector("#global-search").focus(); renderSearch(""); }
  if (modifier && event.key.toLowerCase() === "n") { event.preventDefault(); quickDialog.showModal(); document.querySelector("#task-title").focus(); }
  if (event.key === "Escape") closeDetail();
});

render();
