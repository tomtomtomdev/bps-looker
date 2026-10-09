"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { api, type DomainLevel, type VariablePage } from "@/lib/api/client";

export const EXPLORER_PATH = "/explorer";
export const SEARCH_DEBOUNCE_MS = 300;
export const PAGE_SIZE = 20;

const LEVELS: { value: DomainLevel | ""; label: string }[] = [
  { value: "", label: "All levels" },
  { value: "pusat", label: "National" },
  { value: "prov", label: "Province" },
  { value: "kab", label: "Regency / city" },
];

type SearchState = { q: string; level: DomainLevel | ""; page: number };

function parseLevel(value: string | null): DomainLevel | "" {
  return value === "pusat" || value === "prov" || value === "kab" ? value : "";
}

function parsePage(value: string | null): number {
  const n = Number(value);
  return Number.isInteger(n) && n > 1 ? n : 1;
}

/** `?q=&level=&page=` (defaults omitted) — the shareable URL of a search. */
export function searchQueryString({ q, level, page }: SearchState): string {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (level) params.set("level", level);
  if (page > 1) params.set("page", String(page));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

async function fetchVariables({ q, level, page }: SearchState): Promise<VariablePage> {
  const { data, error } = await api.GET("/variables", {
    params: {
      query: {
        q: q || undefined,
        level: level || undefined,
        page,
        page_size: PAGE_SIZE,
      },
    },
  });
  if (!data) throw new Error(`GET /variables failed: ${JSON.stringify(error)}`);
  return data;
}

const numberFormat = new Intl.NumberFormat("en-US");

/**
 * Explorer search: a debounced search box over `GET /variables`, with level filter and
 * pagination. The state lives in the URL (`?q=&level=&page=`) so a search can be shared.
 */
export function VariableSearch() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [term, setTerm] = useState(() => searchParams.get("q") ?? "");
  const [level, setLevel] = useState(() => parseLevel(searchParams.get("level")));
  // The page belongs to one (query, level): a new search starts again at page 1.
  const q = useDebouncedValue(term, SEARCH_DEBOUNCE_MS).trim();
  const [paging, setPaging] = useState(() => ({
    key: `${(searchParams.get("q") ?? "").trim()}|${level}`,
    page: parsePage(searchParams.get("page")),
  }));
  const key = `${q}|${level}`;
  const page = paging.key === key ? paging.page : 1;
  const setPage = (n: number) => setPaging({ key, page: n });

  const state: SearchState = { q, level, page };
  const href = `${EXPLORER_PATH}${searchQueryString(state)}`;
  const current = `${EXPLORER_PATH}${searchParams.size ? `?${searchParams}` : ""}`;
  useEffect(() => {
    if (href !== current) router.replace(href, { scroll: false });
  }, [href, current, router]);

  const { data, isPending, isError, isFetching, refetch } = useQuery({
    queryKey: ["variables", state],
    queryFn: () => fetchVariables(state),
    placeholderData: keepPreviousData,
  });

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="space-y-6">
      <form
        role="search"
        className="flex flex-col gap-3 sm:flex-row sm:items-center"
        onSubmit={(e) => e.preventDefault()}
      >
        <Input
          type="search"
          aria-label="Search variables"
          placeholder="Search variables, e.g. inflasi, penduduk, kemiskinan…"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          autoFocus
          className="sm:flex-1"
        />
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          Level
          <select
            aria-label="Level"
            value={level}
            onChange={(e) => setLevel(parseLevel(e.target.value))}
            className="h-9 rounded-md border border-input bg-transparent px-2 text-sm text-foreground"
          >
            {LEVELS.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </form>

      {isError ? (
        <div role="alert" className="flex items-center gap-3 rounded-md border p-4 text-sm">
          <span>Could not load variables — is the API running?</span>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            Retry
          </Button>
        </div>
      ) : isPending ? (
        <p className="text-sm text-muted-foreground" role="status">
          Searching…
        </p>
      ) : data.total === 0 ? (
        <p className="text-sm text-muted-foreground" role="status">
          {q ? `No variables match “${q}”.` : "No variables yet."}
        </p>
      ) : (
        <div className="space-y-4" aria-busy={isFetching}>
          <p className="text-sm text-muted-foreground" role="status">
            {numberFormat.format(data.total)} {data.total === 1 ? "variable" : "variables"}
            {pages > 1 ? ` · page ${page} of ${pages}` : ""}
          </p>
          <ul aria-label="Search results" className="divide-y rounded-lg border">
            {data.items.map((v) => (
              <li key={`${v.domain_id}/${v.var_id}`} className="space-y-1 p-4">
                <Link
                  href={`/explorer/${v.domain_id}/${v.var_id}`}
                  className="font-medium hover:underline"
                >
                  {v.title}
                </Link>
                <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                  <Badge variant="secondary">{v.domain_name}</Badge>
                  {v.unit ? <Badge variant="outline">{v.unit}</Badge> : null}
                  {v.subject || v.category ? (
                    <span>{[v.subject, v.category].filter(Boolean).join(" · ")}</span>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
          {pages > 1 ? (
            <nav aria-label="Pagination" className="flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= pages}
                onClick={() => setPage(page + 1)}
              >
                Next
              </Button>
            </nav>
          ) : null}
        </div>
      )}
    </div>
  );
}
