import { expect, test, type Page } from "@playwright/test";

/**
 * Indicators dashboard, browser-level, with the read API mocked by route interception (as in the
 * explorer specs): tiles, province switch, history chart (real ECharts canvas) + Explorer link.
 */
const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const cors = { "access-control-allow-origin": "*" };

function indicator(domain: string, id: number, label: string, extra: Record<string, unknown> = {}) {
  const periode = (extra.periode as string | undefined) ?? "September 2026";
  return {
    domain_id: domain,
    indicator_id: id,
    title: `${label}, ${periode}`,
    label,
    name: null,
    value: 3.28,
    unit: "Persen",
    periode,
    category: 2,
    subject_csa: 536,
    data_source: "BPS",
    first_seen: "2026-10-07T09:00:00Z",
    last_seen: "2026-10-09T09:00:00Z",
    var: 2263,
    variable: { domain_id: domain, var_id: 2263, title: "Inflasi" },
    previous: { periode: "Agustus 2026", value: 3.1 },
    change: 0.18,
    change_pct: 5.81,
    ...extra,
  };
}

const ITEMS: Record<string, ReturnType<typeof indicator>[]> = {
  "0000": [
    indicator("0000", 3, "Inflasi Year on Year"),
    indicator("0000", 20, "Nilai Neraca Perdagangan", {
      periode: "Agustus 2026",
      value: 3552.2,
      unit: "Juta US$",
      var: 498,
      variable: null,
      previous: { periode: "Juli 2026", value: 4170.1 },
      change: -617.9,
      change_pct: -14.82,
    }),
  ],
  "1100": [indicator("1100", 3, "Inflasi Aceh", { value: 1.5, change: -0.2, change_pct: -11.8 })],
};
const NAMES: Record<string, string> = { "0000": "Indonesia", "1100": "Aceh" };

async function mockApi(page: Page) {
  const listRequests: URL[] = [];
  await page.route(`${API}/health`, (route) =>
    route.fulfill({ json: { status: "ok", database: "ok", revision: "0010" }, headers: cors }),
  );
  await page.route(`${API}/domains?**`, (route) =>
    route.fulfill({
      json: [
        { domain_id: "1100", name: "Aceh", url: null, level: "prov" },
        { domain_id: "3100", name: "DKI Jakarta", url: null, level: "prov" },
      ],
      headers: cors,
    }),
  );
  await page.route(`${API}/indicators?**`, (route) => {
    const url = new URL(route.request().url());
    listRequests.push(url);
    const domain = url.searchParams.get("domain") ?? "0000";
    return route.fulfill({
      json: {
        domain: { domain_id: domain, name: NAMES[domain] ?? "DKI Jakarta", url: null, level: "prov" },
        items: ITEMS[domain] ?? [],
      },
      headers: cors,
    });
  });
  await page.route(`${API}/indicators/*/*/history`, (route) => {
    const [, , domain, id] = new URL(route.request().url()).pathname.split("/");
    const item = ITEMS[domain]?.find((i) => i.indicator_id === Number(id));
    if (!item) return route.fulfill({ status: 404, json: { detail: "Indicator not found" }, headers: cors });
    const points = ["Juni 2026", "Juli 2026", "Agustus 2026", item.periode].map((periode, i) => ({
      periode,
      title: `${item.label}, ${periode}`,
      value: item.value - (3 - i) * 0.1,
      first_seen: `2026-10-0${i + 1}T00:00:00Z`,
      last_seen: `2026-10-0${i + 1}T12:00:00Z`,
    }));
    return route.fulfill({ json: { ...item, points }, headers: cors });
  });
  // The Explorer page the link opens (detail only).
  await page.route(`${API}/variables/0000/2263`, (route) =>
    route.fulfill({ status: 404, json: { detail: "Variable not found" }, headers: cors }),
  );
  return listRequests;
}

test("national tiles, open a history chart, follow the Explorer link", async ({ page }) => {
  const requests = await mockApi(page);
  await page.goto("/indicators");

  const inflation = page.getByRole("button", { name: /Inflasi Year on Year/ });
  await expect(inflation).toContainText("3.28");
  await expect(inflation).toContainText("+0.18 (+5.8%) vs Agustus 2026");
  await expect(page.getByRole("button", { name: /Neraca Perdagangan/ })).toContainText(
    "−617.9 (−14.8%) vs Juli 2026",
  );
  expect(requests[0].searchParams.get("domain")).toBe("0000");

  await inflation.click();
  await expect(page).toHaveURL(/\/indicators\?indicator=3$/);
  const panel = page.getByRole("region", { name: "Inflasi Year on Year" });
  await expect(panel.getByRole("figure", { name: /history/ }).locator("canvas")).toBeVisible();

  await panel.getByRole("link", { name: /Open in Explorer/ }).click();
  await expect(page).toHaveURL(/\/explorer\/0000\/2263$/);
});

test("switch to a province; a shared URL restores it", async ({ page }) => {
  const requests = await mockApi(page);
  await page.goto("/indicators");
  await expect(page.getByRole("button", { name: /Inflasi Year on Year/ })).toBeVisible();

  await page.getByRole("combobox", { name: "Region" }).selectOption("1100");
  await expect(page).toHaveURL(/\/indicators\?domain=1100$/);
  await expect(page.getByRole("heading", { name: /Aceh/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Inflasi Aceh/ })).toContainText("1.5");
  expect(requests.at(-1)!.searchParams.get("domain")).toBe("1100");

  await page.goto("/indicators?domain=3100");
  await expect(page.getByText(/No indicators recorded for DKI Jakarta/)).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Region" })).toHaveValue("3100");

  await page.goto("/indicators?indicator=20");
  const panel = page.getByRole("region", { name: "Nilai Neraca Perdagangan" });
  await expect(panel).toContainText("Variable 498 is not crawled yet");
});
