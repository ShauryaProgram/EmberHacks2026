self.addEventListener("push", event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch { payload = { body: event.data?.text() || "" }; }
  event.waitUntil(self.registration.showNotification(payload.title || "Ember reminder", {
    body: payload.body || "You have an upcoming study commitment.",
    tag: payload.tag || "ember-reminder",
    data: payload.data || { url: "/" }
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const target = event.notification.data?.url || "/";
  event.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(windows => {
    const open = windows.find(client => new URL(client.url).origin === self.location.origin);
    if (open) { open.focus(); open.navigate(target === "/calendar" ? "/" : target); return open; }
    return clients.openWindow(target === "/calendar" ? "/" : target);
  }));
});
