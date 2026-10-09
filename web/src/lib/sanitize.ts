import DOMPurify from "dompurify";

/**
 * BPS definition/notes HTML → safe HTML (DOMPurify: no scripts, event handlers, `javascript:`
 * URLs, images or embeds), with the empty `<p><br></p>` padding BPS adds removed.
 * Needs a DOM: on the server (no `window`) it returns "" rather than unsanitized input.
 */
export function sanitizeHtml(html: string | null | undefined): string {
  if (!html || typeof window === "undefined" || !DOMPurify.isSupported) return "";
  const clean = DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "style", "form", "input", "button", "iframe", "svg", "math"],
  });
  return clean.replace(/<p>(?:\s|&nbsp;| |<br\s*\/?>)*<\/p>/gi, "").trim();
}
