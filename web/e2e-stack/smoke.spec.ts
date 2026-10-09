import { expect, test, type Page } from "@playwright/test";

/**
 * Stack smoke: the real web (same-origin /api proxy) → api → Postgres, seeded with the recorded
 * BPS fixtures by `bps seed-fixtures`. Asserts on fixture values, so a change there shows up here:
 * var 2263 = inflation y-on-y, 38 provinces, 2024; 16 national indicators; chapter-03 exports 2024.
 */

async function apiOk(page: Page) {
  await expect(page.getByRole("status").filter({ hasText: /API ok · rev \d+/ })).toBeVisible();
}

test("the browser reaches the API through the same-origin proxy", async ({ request }) => {
  const res = await request.get("/api/health");
  expect(res.status()).toBe(200);
  expect(await res.json()).toMatchObject({ status: "ok", database: "ok" });
});

test("Explorer: search → variable chart → map tab", async ({ page }) => {
  await page.goto("/explorer");
  await apiOk(page);

  await page.getByRole("searchbox", { name: "Search variables" }).fill("inflasi");
  const results = page.getByRole("list", { name: "Search results" });
  const title = "Inflasi Tahunan (Y-on-Y) 38 Provinsi (2022=100)";
  await results.getByRole("link", { name: title }).click();
  await expect(page).toHaveURL(/\/explorer\/0000\/2263$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);

  const chart = page.getByRole("figure", { name: /line chart/ });
  await expect(chart.locator("canvas")).toBeVisible();
  await expect(chart).toHaveAccessibleName(/line chart: INDONESIA$/);

  await page.getByRole("tab", { name: "Map / ranking" }).click();
  await expect(page).toHaveURL(/\?tab=map$/);
  const map = page.getByRole("figure", { name: /choropleth map of provinces/ });
  await expect(map.locator("canvas")).toBeVisible();
  const ranking = page.getByRole("figure", { name: /region ranking/ });
  await expect(ranking).toHaveAccessibleName(/region ranking, Desember 2024: PAPUA PEGUNUNGAN, /);
  await expect(ranking.locator("canvas")).toBeVisible();
});

test("Indicators: tiles → history", async ({ page }) => {
  await page.goto("/indicators");
  await apiOk(page);

  const inflation = page.getByRole("button", { name: /Inflasi Year on Year/ });
  await expect(inflation).toContainText("3.28");
  await inflation.click();
  await expect(page).toHaveURL(/\/indicators\?indicator=\d+$/);
  const panel = page.getByRole("region", { name: /Inflasi Year on Year/ });
  await expect(panel.getByRole("figure", { name: /history/ }).locator("canvas")).toBeVisible();
  await expect(panel.getByRole("link", { name: /Open in Explorer/ })).toHaveAttribute(
    "href",
    "/explorer/0000/2263",
  );
});

test("Trade: summary + breakdown + monthly charts", async ({ page }) => {
  await page.goto("/trade");
  await apiOk(page);

  await expect(page.getByRole("heading", { name: "2024" })).toBeVisible();
  // Chapter-03 exports 2024 (annual rows): US$ 3 995 628 247.
  await expect(page.getByRole("group", { name: "Exports total" })).toContainText("US$3.996B");
  for (const name of [/Top HS chapters/, /Top countries/, /Top ports/]) {
    await expect(page.getByRole("figure", { name }).locator("canvas")).toBeVisible();
  }
  await expect(
    page.getByRole("figure", { name: /monthly/i }).first().locator("canvas"),
  ).toBeVisible();
});
