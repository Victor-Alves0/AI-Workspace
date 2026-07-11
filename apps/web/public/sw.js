/* Service worker do AI Workspace — Web Push.
 * Recebe as notificações push e as exibe; ao clicar, abre/foca o app. */

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "AI Workspace", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "AI Workspace";
  const options = {
    body: data.body || "",
    icon: "/icon.png",
    badge: "/icon.png",
    data: { url: data.url || "/" },
    tag: data.tag || undefined,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const c of clients) {
        if ("focus" in c) {
          c.navigate(url).catch(() => {});
          return c.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
