import React, { useEffect, useState } from "react";
import { Checklist } from "./Checklist";

export function ChecklistSection({ tasks, onToggle }) {
  const open = tasks.filter((task) => !task.done).slice(0, 5);
  const [pending, setPending] = useState(() => new Set());

  useEffect(() => {
    const visible = new Set(open.map((task) => task.id));
    setPending((current) => {
      const next = new Set([...current].filter((id) => visible.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [tasks]);

  const items = open.map((task) => ({
    id: task.id,
    text: task.title,
    done: pending.has(task.id),
  }));

  const complete = (id) => {
    if (pending.has(id)) return;
    setPending((current) => new Set(current).add(id));
    window.setTimeout(() => onToggle(id), 360);
  };

  if (!open.length) {
    return <section className="section task-checklist" aria-labelledby="checklist-title">
      <div className="section-head">
        <h2 id="checklist-title" className="section-label">Checklist</h2>
        <span className="section-count">0</span>
      </div>
      <p className="checklist-empty">No open tasks in this view.</p>
    </section>;
  }

  return <section className="section task-checklist" aria-labelledby="checklist-title">
      <div className="section-head">
        <h2 id="checklist-title" className="section-label">Checklist</h2>
        <span className="section-count">{items.length}</span>
      </div>
      <Checklist items={items} onToggle={complete} corner={0} bounce={30} />
    </section>;
}
