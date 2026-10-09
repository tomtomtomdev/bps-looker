import { describe, expect, it } from "vitest";

import { sanitizeHtml } from "@/lib/sanitize";

describe("sanitizeHtml", () => {
  it("strips scripts, event handlers and javascript: links but keeps formatting", () => {
    const html = sanitizeHtml(
      '<p>Mulai <b>2024</b><br/><script>alert(1)</script><img src=x onerror="alert(2)">' +
        '<a href="javascript:alert(3)">x</a> <a href="https://bps.go.id">BPS</a></p>',
    );
    expect(html).not.toMatch(/script|onerror|javascript:|<img/i);
    expect(html).toContain("<b>2024</b>");
    expect(html).toContain('<a href="https://bps.go.id"');
  });

  it("drops empty paragraphs BPS pads notes with", () => {
    expect(sanitizeHtml("<p><br /></p><p><br></p><p>Isi</p><p>&nbsp;</p>")).toBe("<p>Isi</p>");
  });

  it("handles null", () => {
    expect(sanitizeHtml(null)).toBe("");
  });
});
