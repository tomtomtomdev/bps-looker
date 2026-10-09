import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/app-shell";
import { renderWithQuery } from "@/test/render";

import HomePage from "../page";

const pathname = vi.hoisted(() => ({ current: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

describe("home page in the app shell", () => {
  beforeEach(() => {
    pathname.current = "/";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ status: "ok", database: "ok", revision: "0009" })),
    );
  });

  it("renders the header with the app name and the section nav", () => {
    renderWithQuery(
      <AppShell>
        <HomePage />
      </AppShell>,
    );

    const header = screen.getByRole("banner");
    expect(within(header).getByRole("link", { name: "BPS Looker" })).toHaveAttribute("href", "/");

    const nav = screen.getByRole("navigation", { name: "Main" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["Explorer", "/explorer"],
      ["Indicators", "/indicators"],
      ["Trade", "/trade"],
    ]);
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("marks the current section in the nav", () => {
    pathname.current = "/indicators/0000";
    renderWithQuery(
      <AppShell>
        <p>child</p>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Indicators" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Trade" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText("child")).toBeInTheDocument();
  });
});
