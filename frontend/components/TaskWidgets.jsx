import React from "react";
import { Checklist } from "./Checklist";
import { TodoTower } from "./TodoTower";

export function TaskWidgets({ tasks, onToggle }) {
  const open = tasks.filter((task) => !task.done);
  const urgent = open
    .filter((task) => task.due === "overdue" || task.due === "today")
    .slice(0, 3)
    .map((task) => ({ id: task.id, text: task.title, done: false }));
  const tower = open.slice(0, 8).map((task) => ({ id: task.id, text: task.title }));

  if (!open.length) {
    return <div className="task-widget-empty"><strong>No open tasks.</strong><span>Add one to start a focus list.</span></div>;
  }

  return <div className="task-widgets">
    <section className="task-widget checklist-widget" aria-labelledby="checklist-title">
      <div className="task-widget-heading">
        <div>
          <span className="section-label">Now</span>
          <h2 id="checklist-title">Today’s checklist</h2>
        </div>
        <span>{urgent.length}</span>
      </div>
      {urgent.length
        ? <Checklist items={urgent} onToggle={(id) => window.setTimeout(() => onToggle(id), 240)} corner={14} bounce={36} />
        : <p className="task-widget-note">Nothing due today. The rest of your work stays in the tower.</p>}
    </section>

    <section className="task-widget tower-widget" aria-labelledby="tower-title">
      <div className="task-widget-heading">
        <div>
          <span className="section-label">Open work</span>
          <h2 id="tower-title">Task tower</h2>
        </div>
        <span>{tower.length}</span>
      </div>
      <p className="task-widget-note">Complete a task to pull it from the stack. Drag a row to move the pile.</p>
      <div className="task-tower-frame">
        <TodoTower
          key={tower.map((task) => task.id).join("-")}
          items={tower}
          onComplete={onToggle}
          count={tower.length}
          gravity={42}
          bounce={28}
          slip={32}
        />
      </div>
    </section>
  </div>;
}
