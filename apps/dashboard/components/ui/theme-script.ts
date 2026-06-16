/**
 * theme-script — the pre-hydration no-flash script for the document <head>.
 *
 * Deliberately a NON-"use client" module: it is plain string-building with no React, so a Server
 * Component (app/layout.tsx) can import and call it directly. (A function exported from a
 * "use client" module becomes a client *reference* and cannot be invoked during server render — so
 * this lives apart from theme-provider.tsx, which owns the client ThemeProvider.)
 *
 * Two safety properties:
 *  - The body uses no `<`, `>`, or `&`, so it renders verbatim as the text child of a plain
 *    <script> element (no raw-HTML injection API, no HTML-escaping hazard).
 *  - `storageKey` is embedded via JSON.stringify, so even a crafted key cannot break out of the JS
 *    string literal (defense-in-depth — the key is developer-supplied, not user input).
 */
export function themeScript(storageKey = "theme"): string {
  const key = JSON.stringify(storageKey);
  return (
    "(function(){try{" +
    "var t=localStorage.getItem(" +
    key +
    ");if(!t){t='system';}" +
    "var d=false;" +
    "if(t==='dark'){d=true;}" +
    "if(t==='system'){if(window.matchMedia('(prefers-color-scheme: dark)').matches){d=true;}}" +
    "var c=document.documentElement.classList;" +
    "if(d){c.add('dark');}else{c.remove('dark');}" +
    "}catch(e){}})();"
  );
}
