import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: "Private Trading Intelligence Platform",
  description: "A disciplined control center for paper trading, risk review, and signal monitoring."
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="font-[var(--font-display)]">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
