"use client";

/**
 * Service worker registration.
 *
 * Registers only in production, and actively *unregisters* in development.
 *
 * Why unregister rather than simply not register: a service worker installed by
 * an earlier `next build` keeps serving from cache long after you go back to
 * `next dev`. Next.js dev chunk filenames are stable across rebuilds
 * (`…/dashboard/page.js` rather than a content hash), so a cache-first worker
 * returns pre-edit JavaScript indefinitely. The symptom is bizarre: the UI runs
 * code you deleted, calling API routes that no longer exist, and no amount of
 * reloading helps because the reload is served from the same cache.
 *
 * That is exactly what happened here — a stale bundle kept calling the removed
 * `/api/deadlines/{student_id}` endpoint, got 404s, and bounced the user back to
 * the sign-in page as if authentication had failed.
 */

import { useEffect } from "react";

export default function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;

    if (process.env.NODE_ENV !== "production") {
      // Tear down anything a previous production build left behind, and drop
      // its caches, so development always runs the code actually on disk.
      void navigator.serviceWorker.getRegistrations().then((registrations) => {
        registrations.forEach((registration) => void registration.unregister());
      });
      if ("caches" in window) {
        void caches.keys().then((keys) => keys.forEach((key) => void caches.delete(key)));
      }
      return;
    }

    const onLoad = () => {
      void navigator.serviceWorker.register("/sw.js").then((registration) => {
        // A new worker replaces the old one as soon as it is ready, so a deploy
        // does not leave users on last week's bundle until they close every tab.
        registration.addEventListener("updatefound", () => {
          const installing = registration.installing;
          if (!installing) return;
          installing.addEventListener("statechange", () => {
            if (installing.state === "installed" && navigator.serviceWorker.controller) {
              installing.postMessage({ type: "SKIP_WAITING" });
            }
          });
        });
      });
    };

    window.addEventListener("load", onLoad);
    return () => window.removeEventListener("load", onLoad);
  }, []);

  return null;
}
