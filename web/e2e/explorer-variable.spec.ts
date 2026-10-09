import { expect, test, type Page } from "@playwright/test";

/**
 * Explorer variable page, browser-level, with the read API mocked by route interception
 * (as in explorer-search.spec.ts): the real ECharts canvas renders from mocked series.
 */
const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const cors = { "access-control-allow-origin": "*" };

const MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
  "September", "Oktober", "November", "Desember"];
const REGIONS: Record<number, string> = {
  1100: "PROV ACEH",
  3100: "PROV DKI JAKARTA",
  9999: "INDONESIA",
};

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
  notes: "<p>Mulai Tahun 2024, digunakan tahun dasar 2022.</p><script>window.pwned = true</script>",
  decimal: 2,
  last_update: "2026-10-01T11:24:01",
  vervars: Object.entries(REGIONS).map(([val, label]) => ({
    val: Number(val),
    label,
    group_label: "38 Provinsi (2022=100)",
  })),
  turvars: [{ val: 0, label: "Tidak ada" }],
  turths: [
    ...MONTHS.map((label, i) => ({ val: i + 1, label, freq: "month", has_data: true })),
    { val: 13, label: "Tahunan", freq: "year", has_data: false },
  ],
  periods: [
    { th: 124, label: "2024" },
    { th: 125, label: "2025" },
  ],
};

function series(vervar: number) {
  const points = [124, 125].flatMap((th) =>
    MONTHS.map((_, i) => {
      const year = 1900 + th;
      const month = String(i + 1).padStart(2, "0");
      return {
        period: `${year}-${month}`,
        date: `${year}-${month}-01`,
        th,
        turth: i + 1,
        value: Math.round((2 + Math.sin(i / 2 + vervar / 1000) + (th - 124) / 2) * 100) / 100,
      };
    }),
  );
  return { vervar, vervar_label: REGIONS[vervar], turvar: 0, turvar_label: "Tidak ada", points };
}

async function mockApi(page: Page) {
  const seriesRequests: URL[] = [];
  await page.route(`${API}/health`, (route) =>
    route.fulfill({ json: { status: "ok", database: "ok", revision: "0010" }, headers: cors }),
  );
  await page.route(`${API}/variables/0000/2263`, (route) =>
    route.fulfill({ json: DETAIL, headers: cors }),
  );
  await page.route(`${API}/variables/0000/2263/series?**`, (route) => {
    const url = new URL(route.request().url());
    seriesRequests.push(url);
    const vervars = url.searchParams.getAll("vervar").map(Number);
    return route.fulfill({
      json: { series: vervars.map(series), truncated: false, max_series: 20 },
      headers: cors,
    });
  });
  return seriesRequests;
}

test("open a variable, chart renders, add a region", async ({ page }) => {
  const requests = await mockApi(page);
  await page.goto("/explorer/0000/2263");

  await expect(page.getByRole("heading", { level: 1 })).toHaveText(DETAIL.title);
  await expect(page.getByText("Mulai Tahun 2024")).toBeVisible();
  expect(await page.evaluate(() => "pwned" in window)).toBe(false);

  const chart = page.getByRole("figure", { name: /line chart/ });
  await expect(chart.locator("canvas")).toBeVisible();
  await expect(chart).toHaveAccessibleName(/line chart: INDONESIA$/);
  expect(requests.at(-1)!.searchParams.getAll("vervar")).toEqual(["9999"]);

  const regions = page.getByRole("group", { name: "Region / breakdown" });
  await regions.getByRole("checkbox", { name: "PROV ACEH" }).check();
  await expect(chart).toHaveAccessibleName(/line chart: INDONESIA, PROV ACEH$/);
  await expect(page).toHaveURL(/\/explorer\/0000\/2263\?vervar=9999,1100$/);
  expect(requests.at(-1)!.searchParams.getAll("vervar")).toEqual(["9999", "1100"]);

  await page.getByRole("radio", { name: "Table" }).check();
  await expect(page).toHaveURL(/view=table$/);
  await expect(page.getByRole("table").getByRole("row")).toHaveCount(25);
});

test("a shared URL restores the selection", async ({ page }) => {
  await mockApi(page);
  await page.goto("/explorer/0000/2263?vervar=3100&view=table");
  await expect(page.getByRole("checkbox", { name: "PROV DKI JAKARTA" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "INDONESIA" })).not.toBeChecked();
  await expect(page.getByRole("columnheader", { name: "PROV DKI JAKARTA" })).toBeVisible();
});
