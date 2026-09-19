const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const SHORT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const DEFAULT_PLANNING_PROFILE = {
  version: 5,
  completed: false,
  windows: {
    weekday: { start: "08:00", end: "22:00" },
    weekend: { start: "09:00", end: "22:00" },
  },
  meals: [
    { id: "breakfast", label: "Breakfast", enabled: true, schedule: "daily", start: "08:00", end: "08:30" },
    { id: "lunch", label: "Lunch", enabled: true, schedule: "daily", start: "12:30", end: "13:15" },
    { id: "dinner", label: "Dinner", enabled: true, schedule: "daily", start: "18:30", end: "19:15" },
  ],
  commitments: [],
  preferences: {
    focusBlock: 60,
    buffer: 15,
    dailyLimit: 180,
    noStudyDay: "",
  },
};

export function normalizePlanningProfile(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    ...structuredClone(DEFAULT_PLANNING_PROFILE),
    ...source,
    windows: {
      weekday: { ...DEFAULT_PLANNING_PROFILE.windows.weekday, ...source.windows?.weekday },
      weekend: { ...DEFAULT_PLANNING_PROFILE.windows.weekend, ...source.windows?.weekend },
    },
    meals: Array.isArray(source.meals) ? source.meals : structuredClone(DEFAULT_PLANNING_PROFILE.meals),
    commitments: Array.isArray(source.commitments) ? source.commitments.map(item => ({
      ...item,
      days: Array.isArray(item.days) ? item.days.map(Number) : [],
    })) : [],
    preferences: {
      ...DEFAULT_PLANNING_PROFILE.preferences,
      ...source.preferences,
      focusBlock: Number(source.preferences?.focusBlock ?? DEFAULT_PLANNING_PROFILE.preferences.focusBlock),
      buffer: Number(source.preferences?.buffer ?? DEFAULT_PLANNING_PROFILE.preferences.buffer),
      dailyLimit: Number(source.preferences?.dailyLimit ?? DEFAULT_PLANNING_PROFILE.preferences.dailyLimit),
    },
  };
}

export function createOnboarding({ dialog, profile, onSave, onDismiss }) {
  const content = dialog.querySelector("#setup-content");
  const stepLabel = dialog.querySelector("#setup-step-label");
  const error = dialog.querySelector("#setup-error");
  const back = dialog.querySelector("[data-setup-action='back']");
  const next = dialog.querySelector("[data-setup-action='next']");
  let step = 0;
  let draft = normalizePlanningProfile(profile);

  const steps = [
    { label: "Planning window", title: "Define the edges of your day" },
    { label: "Meals", title: "Protect the time you eat" },
    { label: "Commitments", title: "Add the things study cannot replace" },
    { label: "Preferences", title: "Set the pace of a realistic plan" },
  ];

  const input = ({ label, path, type = "time", value, min, max, stepValue }) => `<label class="setup-field"><span class="field-label">${label}</span><input type="${type}" data-path="${path}" value="${escapeAttribute(value)}"${min ? ` min="${min}"` : ""}${max ? ` max="${max}"` : ""}${stepValue ? ` step="${stepValue}"` : ""}></label>`;
  const option = (value, label, selected) => `<option value="${value}"${String(value) === String(selected) ? " selected" : ""}>${label}</option>`;

  function renderWindow() {
    return `<div class="setup-copy"><p>Semester may place study work only inside these windows. Sleep and everything outside them stay protected automatically.</p></div>
      <div class="setup-rule-group">
        <div class="setup-row-heading"><strong>Monday to Friday</strong><span>Weekday planning window</span></div>
        <div class="setup-time-pair">${input({ label: "Start", path: "windows.weekday.start", value: draft.windows.weekday.start })}${input({ label: "Stop", path: "windows.weekday.end", value: draft.windows.weekday.end })}</div>
      </div>
      <div class="setup-rule-group">
        <div class="setup-row-heading"><strong>Saturday and Sunday</strong><span>Weekend planning window</span></div>
        <div class="setup-time-pair">${input({ label: "Start", path: "windows.weekend.start", value: draft.windows.weekend.start })}${input({ label: "Stop", path: "windows.weekend.end", value: draft.windows.weekend.end })}</div>
      </div>`;
  }

  function renderMeals() {
    return `<div class="setup-copy"><p>Enabled meals become protected blocks every week. Adjust them to how you actually eat, not how an ideal day is supposed to look.</p></div>
      <div class="setup-meals">${draft.meals.map((meal, index) => `<div class="setup-meal-row">
        <label class="setup-check"><input type="checkbox" data-path="meals.${index}.enabled"${meal.enabled ? " checked" : ""}><span>${meal.label}</span></label>
        <label><span class="sr-only">${meal.label} days</span><select data-path="meals.${index}.schedule">${option("daily", "Every day", meal.schedule)}${option("weekdays", "Weekdays", meal.schedule)}${option("weekends", "Weekends", meal.schedule)}</select></label>
        <label><span class="sr-only">${meal.label} start</span><input type="time" data-path="meals.${index}.start" value="${escapeAttribute(meal.start)}"></label>
        <span class="setup-time-separator">to</span>
        <label><span class="sr-only">${meal.label} end</span><input type="time" data-path="meals.${index}.end" value="${escapeAttribute(meal.end)}"></label>
      </div>`).join("")}</div>`;
  }

  function renderDays(days, index) {
    return SHORT_DAYS.map((day, dayIndex) => `<label class="day-choice"><input type="checkbox" data-days="commitments.${index}.days" value="${dayIndex}"${days.includes(dayIndex) ? " checked" : ""}><span>${day}</span></label>`).join("");
  }

  function renderCommitments() {
    const rows = draft.commitments.length ? draft.commitments.map((item, index) => `<fieldset class="commitment-row">
      <legend class="sr-only">Commitment ${index + 1}</legend>
      <div class="commitment-main">
        <label class="setup-field commitment-name"><span class="field-label">Name</span><input data-path="commitments.${index}.title" value="${escapeAttribute(item.title)}" placeholder="Club meeting, work shift, commute"></label>
        <label class="setup-field"><span class="field-label">Type</span><select data-path="commitments.${index}.category">${["Club", "Work", "Commute", "Exercise", "Care", "Personal"].map(value => option(value, value, item.category)).join("")}</select></label>
        <button class="text-button setup-remove" type="button" data-remove-commitment="${index}">Remove</button>
      </div>
      <div class="day-choices" aria-label="Days for ${escapeAttribute(item.title || `commitment ${index + 1}`)}">${renderDays(item.days, index)}</div>
      <div class="setup-time-pair compact">${input({ label: "Start", path: `commitments.${index}.start`, value: item.start })}${input({ label: "End", path: `commitments.${index}.end`, value: item.end })}</div>
    </fieldset>`).join("") : `<div class="setup-empty"><strong>No recurring commitments added.</strong><p>Add clubs, work, commuting, exercise, care, or any other time the planner must leave alone.</p></div>`;
    return `<div class="setup-copy"><p>Classes are already in the calendar. Add the rest of your fixed week here.</p></div>${rows}<button class="text-button setup-add" type="button" data-setup-action="add-commitment">Add commitment</button>`;
  }

  function renderPreferences() {
    const protectedCount = draft.meals.filter(meal => meal.enabled).length + draft.commitments.length + (draft.preferences.noStudyDay !== "" ? 1 : 0);
    return `<div class="setup-copy"><p>These values shape new study blocks. They do not change deadlines or class times.</p></div>
      <div class="setup-preferences">
        <label class="setup-field"><span class="field-label">Default focus block</span><select data-path="preferences.focusBlock">${[30, 45, 60, 90].map(value => option(value, `${value} minutes`, draft.preferences.focusBlock)).join("")}</select></label>
        <label class="setup-field"><span class="field-label">Buffer between plans</span><select data-path="preferences.buffer">${[0, 10, 15, 30].map(value => option(value, `${value} minutes`, draft.preferences.buffer)).join("")}</select></label>
        <label class="setup-field"><span class="field-label">Daily study limit</span><select data-path="preferences.dailyLimit">${[120, 180, 240, 300].map(value => option(value, `${value / 60} hours`, draft.preferences.dailyLimit)).join("")}</select></label>
        <label class="setup-field"><span class="field-label">No-study day</span><select data-path="preferences.noStudyDay">${option("", "None", draft.preferences.noStudyDay)}${DAY_NAMES.map((day, index) => option(index, day, draft.preferences.noStudyDay)).join("")}</select></label>
      </div>
      <div class="setup-summary"><span class="section-label">Planner result</span><strong>${protectedCount} recurring protections</strong><p>Study blocks will use ${draft.preferences.focusBlock}-minute sessions with ${draft.preferences.buffer} minutes between plans.</p></div>`;
  }

  function render() {
    const current = steps[step];
    stepLabel.textContent = `0${step + 1} / 0${steps.length} · ${current.label}`;
    dialog.querySelector("#setup-title").textContent = current.title;
    dialog.querySelectorAll("[data-step-indicator]").forEach((indicator, index) => {
      indicator.classList.toggle("is-active", index === step);
      indicator.classList.toggle("is-complete", index < step);
      if (index === step) indicator.setAttribute("aria-current", "step");
      else indicator.removeAttribute("aria-current");
    });
    content.innerHTML = [renderWindow, renderMeals, renderCommitments, renderPreferences][step]();
    back.hidden = step === 0;
    next.textContent = step === steps.length - 1 ? "Save and open planner" : "Continue";
    error.hidden = true;
  }

  function syncDraft() {
    content.querySelectorAll("[data-path]").forEach(control => {
      const value = control.type === "checkbox" ? control.checked : control.value;
      setPath(draft, control.dataset.path, value);
    });
    const dayGroups = new Map();
    content.querySelectorAll("[data-days]").forEach(control => {
      if (!dayGroups.has(control.dataset.days)) dayGroups.set(control.dataset.days, []);
      if (control.checked) dayGroups.get(control.dataset.days).push(Number(control.value));
    });
    dayGroups.forEach((days, path) => setPath(draft, path, days));
    draft.preferences.focusBlock = Number(draft.preferences.focusBlock);
    draft.preferences.buffer = Number(draft.preferences.buffer);
    draft.preferences.dailyLimit = Number(draft.preferences.dailyLimit);
  }

  function validate() {
    syncDraft();
    const ranges = step === 0
      ? [draft.windows.weekday, draft.windows.weekend]
      : step === 1
        ? draft.meals.filter(meal => meal.enabled)
        : step === 2
          ? draft.commitments
          : [];
    if (ranges.some(range => !range.start || !range.end || range.start >= range.end)) return "Every start time must be before its end time.";
    if (step === 2 && draft.commitments.some(item => !item.title.trim() || !item.days.length)) return "Each commitment needs a name and at least one day.";
    return "";
  }

  dialog.addEventListener("click", event => {
    const remove = event.target.closest("[data-remove-commitment]");
    if (remove) {
      syncDraft();
      draft.commitments.splice(Number(remove.dataset.removeCommitment), 1);
      render();
      return;
    }
    const action = event.target.closest("[data-setup-action]")?.dataset.setupAction;
    if (!action) return;
    if (action === "add-commitment") {
      syncDraft();
      draft.commitments.push({ id: crypto.randomUUID(), title: "", category: "Club", days: [], start: "17:00", end: "18:00" });
      render();
    }
    if (action === "back") {
      syncDraft();
      step = Math.max(0, step - 1);
      render();
    }
    if (action === "dismiss") {
      syncDraft();
      dialog.close();
      onDismiss?.();
    }
    if (action === "next") {
      const message = validate();
      if (message) {
        error.textContent = message;
        error.hidden = false;
        return;
      }
      if (step < steps.length - 1) {
        step += 1;
        render();
        content.querySelector("input, select, button")?.focus();
        return;
      }
      draft.completed = true;
      draft.version = 5;
      draft.updatedAt = new Date().toISOString();
      onSave(structuredClone(draft));
      dialog.close();
    }
  });

  dialog.addEventListener("cancel", () => onDismiss?.());

  return {
    open(nextProfile = profile) {
      draft = normalizePlanningProfile(nextProfile);
      step = 0;
      render();
      if (!dialog.open) dialog.showModal();
    },
  };
}

function setPath(target, path, value) {
  const parts = path.split(".");
  let cursor = target;
  parts.slice(0, -1).forEach(part => { cursor = cursor[part]; });
  cursor[parts.at(-1)] = value;
}

function escapeAttribute(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}
