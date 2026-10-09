import type { Metadata } from "next";

import { AppShell } from "@/components/app-shell";
import { APP_NAME } from "@/lib/site";

import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: APP_NAME, template: `%s · ${APP_NAME}` },
  description: "Explore BPS (Statistics Indonesia) data: dynamic tables, indicators and trade.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
