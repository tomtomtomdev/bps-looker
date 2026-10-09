import { expect, test, type Page } from "@playwright/test";

import regions from "../src/test/bps-regions.json" with { type: "json" };

/**
 * Explorer Map / ranking tab, browser-level: the read API is mocked by route interception (as in
 * the other specs); the map shapes are the real `public/geo/` files served by the app.
 */
const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const cors = { "access-control-allow-origin": "*" };

const PROVINCES = Object.entries(regions.provinces38).map(([code, name]) => ({
  val: Number(code),
  label: `PROV ${name}`,
}));

const DETAIL = {
  domain_id: "0000",
  domain_name: "Indonesia",
  domain_level: "pusat",
  var_id: 2263,
  title: "Inflasi Tahunan (Y-on-Y) 38 Provinsi (2022=100)",
  unit: "Persen",
  subject_id: 3,
  subject: "Inflasi",
  category: "Harga-Harga",
  definition: null,
  notes: null,
  decimal: 2,
  last_update: "2026-10-01T11:24:01",
  vervars: [...PROVINCES, { val: 9999, label: "INDONESIA" }].map((v) => ({
    ...v,
    group_label: "38 Provinsi (2022=100)",
  })),
  turvars: [{ val: 0, label: "Tidak ada" }],
  turths: [
    { val: 1, label: "Januari", freq: "month", has_data: true },
    { val: 2, label: "Februari", freq: "month", has_data: true },
    { val: 13, label: "Tahunan", freq: "year", has_data: false },
  ],
  periods: [{ th: 126, label: "2026" }],
};

const PERIODS = [
  { th: 126, turth: 1, period: "2026-01", date: "2026-01-01", label: "Januari 2026" },
  { th: 126, turth: 2, period: "2026-02", date: "2026-02-01", label: "Februari 2026" },
];

/** Values: Papua Pegunungan (9700) highest; in January it's DKI Jakarta (3100). */
function crossSection(turth: number) {
  const value = (val: number) =>
    turth === 1 && val === 3100 ? 9 : Math.round((1 + (val / 9700) * 4) * 100) / 100;
  const rows = PROVINCES.map((p) => ({ vervar: p.val, label: p.label, value: value(p.val) }));
  rows.sort((a, b) => b.value - a.value);
  return {
    turvar: 0,
    turvar_label: "Tidak ada",
    period: PERIODS[turth - 1],
    periods: PERIODS,
    regions: rows,
    national: { vervar: 9999, label: "INDONESIA", value: 2.65 },
  };
}

async function mockApi(page: Page) {
  const requests: URL[] = [];
  await page.route(`${API}/health`, (route) =>
    route.fulfill({ json: { status: "ok", database: "ok", revision: "0010" }, headers: cors }),
  );
  await page.route(`${API}/variables/0000/2263`, (route) =>
    route.fulfill({ json: DETAIL, headers: cors }),
  );
  await page.route(`${API}/variables/0000/2263/cross-section?**`, (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    return route.fulfill({
      json: crossSection(Number(url.searchParams.get("turth") ?? 2)),
      headers: cors,
    });
  });
  await page.route(`${API}/variables/0000/2263/series?**`, (route) => {
    const vervars = new URL(route.request().url()).searchParams.getAll("vervar").map(Number);
    const series = vervars.map((v) => ({
      vervar: v,
      vervar_label: PROVINCES.find((p) => p.val === v)?.label ?? "INDONESIA",
      turvar: 0,
      turvar_label: "Tidak ada",
      points: PERIODS.map((p) => ({ ...p, value: 2 })),
    }));
    return route.fulfill({ json: { series, truncated: false, max_series: 20 }, headers: cors });
  });
  return requests;
}

test("map + ranking of a 38-province variable; slider; click a bar opens its series", async ({
  page,
}) => {
  const requests = await mockApi(page);
  await page.goto("/explorer/0000/2263");
  await page.getByRole("tab", { name: "Map / ranking" }).click();
  await expect(page).toHaveURL(/\?tab=map$/);

  const map = page.getByRole("figure", { name: /choropleth map of provinces, Februari 2026/ });
  await expect(map.locator("canvas")).toBeVisible();
  const ranking = page.getByRole("figure", { name: /region ranking/ });
  await expect(ranking).toHaveAccessibleName(/region ranking, Februari 2026: PAPUA PEGUNUNGAN, PAPUA TENGAH, PAPUA SELATAN, …$/);
  await expect(ranking.locator("canvas")).toBeVisible();
  await expect(page.getByText("INDONESIA: 2.65 Persen")).toBeVisible();
  expect(requests.at(-1)!.searchParams.get("freq")).toBe("month");

  // The 38-province shapes loaded from public/geo.
  const geo = await page.request.get("/geo/provinces-38.json");
  expect((await geo.json()).features).toHaveLength(38);

  const slider = page.getByRole("slider", { name: "Period" });
  await slider.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page).toHaveURL(/tab=map&th=126&turth=1$/);
  await expect(ranking).toHaveAccessibleName(/region ranking, Januari 2026: DKI JAKARTA,/);

  // Click the top bar (DKI Jakarta in January: 9 of a 0-10 axis): just right of the labels.
  const box = (await ranking.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.55, box.y + 59);
  await expect(page).toHaveURL(/\/explorer\/0000\/2263\?vervar=3100$/);
  await expect(page.getByRole("tab", { name: "Time series" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("checkbox", { name: "PROV DKI JAKARTA" })).toBeChecked();
});

test("clicking a province on the map opens its series", async ({ page }) => {
  await mockApi(page);
  await page.goto("/explorer/0000/2263?tab=map");
  const map = page.getByRole("figure", { name: /choropleth map/ });
  await expect(map.locator("canvas")).toBeVisible();
  // Kalimantan sits in the middle of the map; click a point inside Kalimantan Tengah.
  const box = (await map.boundingBox())!;
  await page.waitForTimeout(300);
  await page.mouse.click(box.x + box.width * 0.36, box.y + box.height * 0.42);
  await expect(page).toHaveURL(/\/explorer\/0000\/2263\?vervar=6\d00$/);
});
