import { expect, test, type Page } from "@playwright/test";

/**
 * Trade dashboard, browser-level, with the read API mocked by route interception (as in the
 * other specs): totals, flow toggle, month range, top-N bars (real ECharts canvas), chapter
 * filter by clicking a bar, URL state.
 */
const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const cors = { "access-control-allow-origin": "*" };

const PERIODS = {
  items: [
    { flow: "export", year: 2024, annual: true, months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] },
    { flow: "export", year: 2025, annual: true, months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] },
    { flow: "import", year: 2024, annual: true, months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] },
    { flow: "import", year: 2025, annual: true, months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] },
  ],
  latest_year: 2025,
  latest_month: "2025-12",
};

const ITEMS = {
  hs2: [["27", "Mineral fuels"], ["15", "Animal or vegetable fats"], ["72", "Iron and steel"]],
  country: [["CHINA", "CHINA"], ["UNITED STATES", "UNITED STATES"], ["JAPAN", "JAPAN"]],
  port: [["TANJUNG PRIOK", "TANJUNG PRIOK"], ["DUMAI", "DUMAI"], ["", null]],
} as Record<string, [string, string | null][]>;

function rangeOf(url: URL) {
  const from = url.searchParams.get("from") ?? "2025";
  const to = url.searchParams.get("to") ?? from;
  return { start: from, end: to, granularity: from.includes("-") ? "month" : "year" };
}

async function mockApi(page: Page) {
  const seen: URL[] = [];
  await page.route(`${API}/health`, (route) =>
    route.fulfill({ json: { status: "ok", database: "ok", revision: "0011" }, headers: cors }),
  );
  await page.route(`${API}/trade/**`, (route) => {
    const url = new URL(route.request().url());
    seen.push(url);
    const flow = url.searchParams.get("flow") ?? "export";
    const scale = flow === "import" ? 0.9 : 1;
    switch (url.pathname) {
      case "/trade/periods":
        return route.fulfill({ json: PERIODS, headers: cors });
      case "/trade/summary":
        return route.fulfill({
          json: {
            range: rangeOf(url),
            items: [
              { flow: "export", value_usd: 282.5e9, netweight_kg: 7e11, periods: [] },
              { flow: "import", value_usd: 241.4e9, netweight_kg: 2e11, periods: [] },
            ],
            balance_usd: 41.1e9,
          },
          headers: cors,
        });
      case "/trade/breakdown": {
        const by = url.searchParams.get("by")!;
        return route.fulfill({
          json: {
            by,
            flow,
            top: 10,
            range: rangeOf(url),
            total: { flow, value_usd: 100e9 * scale, netweight_kg: 1e9, periods: [] },
            items: ITEMS[by].map(([key, label], i) => ({
              key,
              label,
              value_usd: (50 - i * 15) * 1e9 * scale,
              netweight_kg: 1e8,
              share: 0.5 - i * 0.15,
            })),
            others: { count: 30, value_usd: 5e9, netweight_kg: 1e7, share: 0.05 },
          },
          headers: cors,
        });
      }
      case "/trade/series": {
        const hs2 = url.searchParams.get("hs2");
        const months = Array.from({ length: 12 }, (_, i) => `2025-${String(i + 1).padStart(2, "0")}`);
        const points = (base: number) =>
          months.map((period, i) => ({ period, date: `${period}-01`, value_usd: (base + i) * 1e9, netweight_kg: 1 }));
        return route.fulfill({
          json: {
            hs2,
            hs2_label: hs2 ? "Mineral fuels" : null,
            country: url.searchParams.get("country"),
            range: null,
            series: [
              { flow: "export", points: points(22) },
              { flow: "import", points: points(19) },
            ],
            balance: months.map((period) => ({ period, date: `${period}-01`, value_usd: 3e9 })),
          },
          headers: cors,
        });
      }
    }
    return route.fulfill({ status: 404, json: { detail: "Not Found" }, headers: cors });
  });
  return seen;
}

const chartCanvas = (page: Page, name: RegExp) => page.getByRole("figure", { name }).locator("canvas");

test("totals, top-N charts and monthly charts; toggle imports; month range", async ({ page }) => {
  const seen = await mockApi(page);
  await page.goto("/trade");

  await expect(page.getByRole("group", { name: "Exports total" })).toContainText("US$282.5B");
  await expect(page.getByRole("group", { name: "Trade balance" })).toContainText("US$41.1B");
  for (const name of [/Top countries/, /Top ports/, /Top HS chapters/, /Monthly exports/, /Trade balance by month/]) {
    await expect(chartCanvas(page, name)).toBeVisible();
  }

  await page.getByRole("group", { name: "Flow" }).getByRole("button", { name: "Imports" }).click();
  await expect(page).toHaveURL(/\/trade\?flow=import$/);
  await expect(chartCanvas(page, /Monthly imports/)).toBeVisible();
  await expect.poll(() => seen.some((u) => u.pathname === "/trade/breakdown" && u.searchParams.get("flow") === "import")).toBe(true);

  await page.getByRole("combobox", { name: "Period" }).selectOption("month");
  await expect(page).toHaveURL(/from=2025-01&to=2025-12/);
  await page.getByRole("combobox", { name: "From" }).selectOption("2025-04");
  await expect(page).toHaveURL(/\/trade\?flow=import&from=2025-04&to=2025-12$/);
  await expect(page.getByRole("heading", { name: "Apr 2025 – Dec 2025" })).toBeVisible();
});

test("click a chapter bar to filter the monthly charts; a shared URL restores it", async ({ page }) => {
  const seen = await mockApi(page);
  await page.goto("/trade");
  const chapters = page.getByRole("figure", { name: /Top HS chapters/ });
  await expect(chapters.locator("canvas")).toBeVisible();
  await page.waitForTimeout(300); // let ECharts lay out the bars

  // The first (largest) bar sits at the top of the plot, starting at the left of the value axis.
  const box = (await chapters.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.62, box.y + 16);
  await expect(page).toHaveURL(/\/trade\?hs2=27$/);
  await expect(page.getByText("Chapter 27 Mineral fuels")).toBeVisible();
  await expect.poll(() => seen.filter((u) => u.pathname === "/trade/series").at(-1)?.searchParams.get("hs2")).toBe("27");

  await page.getByRole("button", { name: "Clear chapter filter" }).click();
  await expect(page).toHaveURL(/\/trade$/);

  await page.goto("/trade?flow=import&from=2024&to=2025&country=CHINA");
  await expect(page.getByRole("combobox", { name: "From" })).toHaveValue("2024");
  await expect(page.getByRole("button", { name: "Imports" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Country CHINA")).toBeVisible();
  await expect(page.getByRole("heading", { name: "2024–2025" })).toBeVisible();
});
