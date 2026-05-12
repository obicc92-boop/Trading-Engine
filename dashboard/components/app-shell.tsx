"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { startTransition, useEffect, type ReactNode } from "react";
import {
  Activity,
  ArrowLeftRight,
  BellRing,
  BrainCircuit,
  BookMarked,
  Eye,
  Gauge,
  LayoutDashboard,
  LineChart,
  Settings,
  ShieldCheck
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";


const AUTO_REFRESH_INTERVAL_MS = 15_000;

const navigation = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/workflow", label: "Daily Workflow", icon: Activity },
  { href: "/positions", label: "Trade Monitor", icon: Eye },
  { href: "/signals", label: "Signals", icon: Gauge },
  { href: "/coach", label: "AI Coach", icon: BrainCircuit },
  { href: "/journal", label: "Trade Journal", icon: BookMarked },
  { href: "/performance", label: "Performance", icon: LineChart },
  { href: "/compare", label: "Paper vs Live", icon: ArrowLeftRight },
  { href: "/review", label: "Review Lab", icon: ShieldCheck },
  { href: "/telegram", label: "Telegram Alerts", icon: BellRing },
  { href: "/settings", label: "Settings", icon: Settings }
];

const mobileDockNavigation = navigation.filter((item) =>
  ["/", "/workflow", "/positions", "/signals", "/review"].includes(item.href)
);

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const activeItem = navigation.find((item) => item.href === pathname) ?? navigation[0];

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      if (document.visibilityState !== "visible" || !navigator.onLine) {
        return;
      }

      startTransition(() => {
        router.refresh();
      });
    }, AUTO_REFRESH_INTERVAL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [router]);

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,rgba(44,168,163,0.16),transparent_30%),linear-gradient(180deg,#02060c_0%,#07101a_55%,#02060c_100%)] text-white">
      <div className="mx-auto max-w-[1600px] px-3 py-3 sm:px-4 sm:py-4 lg:grid lg:min-h-screen lg:grid-cols-[280px_minmax(0,1fr)] lg:gap-6 lg:px-6">
        <div
          className="sticky top-0 z-40 -mx-3 mb-4 border-b border-white/10 bg-slate-950/85 px-3 pb-3 backdrop-blur-xl sm:-mx-4 sm:px-4 lg:hidden"
          style={{ paddingTop: "calc(0.75rem + var(--safe-top))" }}
        >
          <div className="rounded-[24px] border border-white/10 bg-white/[0.04] p-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-[11px] uppercase tracking-[0.22em] text-cyan-200/70">Private Trading Desk</p>
                <h1 className="mt-2 text-lg font-semibold leading-tight">Trading Intelligence Platform</h1>
              </div>
              <div className="flex flex-col items-end gap-2">
                <Badge variant="accent" className="shrink-0">
                  {activeItem.label}
                </Badge>
                <Badge variant="neutral" className="shrink-0">
                  Refreshes every 15s
                </Badge>
              </div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/65">
              Phone-first monitoring for signals, open paper trades, and review loops.
            </p>
          </div>

          <nav className="no-scrollbar mt-3 overflow-x-auto">
            <div className="flex min-w-max gap-2 pb-1">
              {navigation.map((item) => {
                const Icon = item.icon;
                const active = pathname === item.href;

                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      "flex items-center gap-2 rounded-full border px-3 py-2 text-xs font-medium transition",
                      active
                        ? "border-cyan-300/20 bg-cyan-300/12 text-white"
                        : "border-white/10 bg-white/[0.03] text-white/65"
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          </nav>
        </div>

        <aside className="hidden rounded-[28px] border border-white/10 bg-slate-950/80 p-5 shadow-glow backdrop-blur-xl lg:block">
          <div className="space-y-4">
            <div>
              <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Private Trading Desk</p>
              <h1 className="mt-3 text-2xl font-semibold leading-tight">Trading Intelligence Platform</h1>
            </div>
            <Badge variant="accent">Capital protection first</Badge>
          </div>

          <nav className="mt-8 space-y-2">
            {navigation.map((item) => {
              const Icon = item.icon;
              const active = pathname === item.href;

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "flex items-center gap-3 rounded-2xl px-4 py-3 text-sm transition",
                    active
                      ? "bg-cyan-300/12 text-white shadow-[inset_0_0_0_1px_rgba(129,248,233,0.15)]"
                      : "text-white/65 hover:bg-white/5 hover:text-white"
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </nav>

          <div className="mt-10 rounded-2xl border border-emerald-300/10 bg-emerald-300/5 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-emerald-100/70">Operating posture</p>
            <p className="mt-2 text-sm text-white/75">
              Paper trading is the training ground. Review expectancy, drawdown, and exit quality before you even think about live exposure.
            </p>
            <Badge variant="neutral" className="mt-4">
              Dashboard auto-refreshes every 15s
            </Badge>
          </div>
        </aside>

        <main className="overflow-hidden rounded-[24px] border border-white/10 bg-slate-950/50 backdrop-blur-xl sm:rounded-[32px]">
          <div className="min-h-screen bg-grid bg-[size:28px_28px] bg-[position:-1px_-1px] px-3 py-4 pb-24 sm:px-4 sm:py-5 sm:pb-28 lg:px-6 lg:pb-5">
            {children}
          </div>
        </main>
      </div>

      <div
        className="fixed inset-x-0 bottom-0 z-40 border-t border-white/10 bg-slate-950/92 px-3 pt-2 backdrop-blur-xl lg:hidden"
        style={{ paddingBottom: "calc(0.65rem + var(--safe-bottom))" }}
      >
        <nav className="mx-auto grid max-w-xl grid-cols-5 gap-1">
          {mobileDockNavigation.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href;

            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex flex-col items-center gap-1 rounded-2xl px-2 py-2 text-[11px] transition",
                  active ? "bg-cyan-300/12 text-white" : "text-white/60"
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="text-center leading-tight">{item.label.split(" ")[0]}</span>
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
