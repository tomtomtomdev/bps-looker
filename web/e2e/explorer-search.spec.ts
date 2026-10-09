import { expect, test, type Page } from "@playwright/test";

/**
 * Explorer search, browser-level: the read API is mocked with route interception (the app calls
 * it from the browser), so this runs without Python/Postgres. The real-stack smoke (compose +
 * seeded DB) is U7.
 */
const API = process.env.E2E_API_URL ?? "http://localhost:8000";

const INFLASI = [
  {
    domain_id: "0000",
    domain_name: "Indonesia",
    domain_level: "pusat",
    var_id: 2263,
    title: "Inflasi Bulanan (M-to-M) Indonesia",
    unit: "Persen",
    subject_id: 3,
    subject: "Inflasi",
    category: "Harga-Harga",
  },
  {
    domain_id: "3100",
    domain_name: "DKI Jakarta",
    domain_level: "prov",
    var_id: 1,
    title: "Inflasi Jakarta",
    unit: "Persen",
    subject_id: 3,
    subject: "Inflasi",
    category: "Harga-Harga",
  },
];

async function mockApi(page: Page) {
  const cors = { "access-control-allow-origin": "*" };
  await page.route(`${API}/health`, (route) =>
    route.fulfill({ json: { status: "ok", database: "ok", revision: "0010" }, headers: cors }),
  );
  await page.route(`${API}/variables?**`, (route) => {
    const url = new URL(route.request().url());
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const items = q ? INFLASI.filter((v) => v.title.toLowerCase().includes(q)) : INFLASI;
    return route.fulfill({
      json: { items, total: items.length, page: 1, page_size: 20 },
      headers: cors,
    });
  });
}

test("search inflasi, open a variable", async ({ page }) => {
  await mockApi(page);
  await page.goto("/explorer");
  await expect(page.getByRole("status").filter({ hasText: "API ok" })).toBeVisible();

  await page.getByRole("searchbox", { name: "Search variables" }).fill("inflasi");
  await expect(page).toHaveURL(/\/explorer\?q=inflasi$/);
  const results = page.getByRole("list", { name: "Search results" });
  await expect(results.getByRole("listitem")).toHaveCount(2);
  await expect(results.getByRole("listitem").first()).toContainText("Persen");
  await expect(results.getByRole("listitem").first()).toContainText("Inflasi · Harga-Harga");

  await results.getByRole("link", { name: "Inflasi Bulanan (M-to-M) Indonesia" }).click();
  await expect(page).toHaveURL(/\/explorer\/0000\/2263$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Variable 2263 · domain 0000");
});

test("a shared search URL restores the results", async ({ page }) => {
  await mockApi(page);
  await page.goto("/explorer?q=jakarta");
  await expect(page.getByRole("searchbox", { name: "Search variables" })).toHaveValue("jakarta");
  await expect(page.getByRole("link", { name: "Inflasi Jakarta" })).toHaveAttribute(
    "href",
    "/explorer/3100/1",
  );
  await expect(page.getByText("No variables match")).toHaveCount(0);
});

test("empty and error states", async ({ page }) => {
  await mockApi(page);
  await page.goto("/explorer?q=zzz");
  await expect(page.getByText("No variables match “zzz”.")).toBeVisible();

  await page.route(`${API}/variables?**`, (route) => route.abort());
  await page.getByRole("searchbox", { name: "Search variables" }).fill("inflasi");
  // TanStack Query retries 3 times (1 + 2 + 4 s backoff) before showing the error.
  await expect(
    page.getByRole("alert").filter({ hasText: "Could not load variables" }),
  ).toBeVisible({ timeout: 15_000 });
});
