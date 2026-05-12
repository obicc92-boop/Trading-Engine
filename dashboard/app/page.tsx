import { Suspense } from "react";
import { ArrowUpRight, ShieldCheck, Sparkles } from "lucide-react";

import { LazyTradingViewPanel } from "@/components/lazy-tradingview-panel";
import { MetricCard } from "@/components/metric-card";
import { ModeTabs } from "@/components/mode-tabs";
import { PerformanceChart } from "@/components/performance-chart";
import { SignalsTable } from "@/components/signals-table";
import { TradesTable } from "@/components/trades-table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getDashboardSettings,
  getRecentPerformanceLogs,
  getRecentSignals,
  getRecentTrades
} from "@/lib/data";
import { formatCurrency } from "@/lib/utils";

type DashboardSettings = ReturnType<typeof getDashboardSettings>;
type PageProps = {
  searchParams?: Promise<{
    mode?: string;
  }>;
};

export default async function DashboardPage({ searchParams }: PageProps) {
  const settings = getDashboardSettings();
  const params = (await searchParams) ?? {};
  const activeMode = params.mode === "live" ? "live" : "paper";

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-2xl">
          <p className="text-xs uppercase tracking-[0.26em] text-cyan-200/70">Control center</p>
          <h2 className="mt-3 text-2xl font-semibold leading-tight sm:text-3xl lg:text-5xl">
            Calm, risk-aware trade decisions for a private spot workflow.
          </h2>
          <p className="mt-4 max-w-xl text-sm leading-6 text-white/65">
            This platform is built to reduce impulse, filter weak setups, and keep execution controlled. Signals are only useful when they support discipline.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <ModeTabs pathname="/" activeMode={activeMode} />
          <Badge variant="accent">{settings.executionMode.toUpperCase()} MODE</Badge>
          <Badge variant="neutral">{settings.bybitEnvironment.toUpperCase()}</Badge>
          <Badge variant="positive">Risk-per-trade {settings.riskPerTrade}</Badge>
        </div>
      </section>

      <Suspense fallback={<MetricsSectionSkeleton />}>
        <DashboardMetricsSection settings={settings} activeMode={activeMode} />
      </Suspense>

      <section className="grid gap-6 xl:grid-cols-[1.35fr_0.9fr]">
        <Suspense fallback={<PanelSkeleton className="h-[534px]" />}>
          <DashboardMarketFocusSection />
        </Suspense>
        <Suspense fallback={<PanelSkeleton className="h-[534px]" />}>
          <DashboardSignalQueueSection />
        </Suspense>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Suspense fallback={<PanelSkeleton className="h-[476px]" />}>
          <DashboardPerformanceSection activeMode={activeMode} />
        </Suspense>
        <Suspense fallback={<PanelSkeleton className="h-[476px]" />}>
          <DashboardTradesSection activeMode={activeMode} />
        </Suspense>
      </section>

      <Suspense fallback={<PanelSkeleton className="h-[420px]" />}>
        <DashboardSignalsLedgerSection />
      </Suspense>
    </div>
  );
}

async function DashboardMetricsSection({ settings, activeMode }: { settings: DashboardSettings; activeMode: "paper" | "live" }) {
  const latestPerformance = (await getRecentPerformanceLogs(activeMode))[0];

  return (
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        title="Total Equity"
        value={formatCurrency(latestPerformance?.total_equity ?? 0)}
        description={`${activeMode === "paper" ? "Paper" : "Live"} portfolio equity after realized and unrealized changes.`}
        footnote={<Badge variant="accent">{activeMode.toUpperCase()} lane</Badge>}
      />
      <MetricCard
        title="Available Cash"
        value={formatCurrency(latestPerformance?.available_cash ?? 0)}
        description={`Cash currently free for new ${activeMode} positions after open trade allocation.`}
        footnote={<Badge variant="neutral">No leverage</Badge>}
      />
      <MetricCard
        title="Daily PnL"
        value={formatCurrency(latestPerformance?.realized_pnl_today ?? 0)}
        description={`Realized ${activeMode} performance today, used against the daily loss circuit breaker.`}
        footnote={<Badge variant={(latestPerformance?.realized_pnl_today ?? 0) >= 0 ? "positive" : "negative"}>{settings.dailyLossLimit} max drawdown</Badge>}
      />
      <MetricCard
        title="Open Positions"
        value={String(latestPerformance?.open_positions ?? 0)}
        description={`Active ${activeMode} positions currently being managed by the engine.`}
        footnote={<Badge variant="neutral">Cap {settings.maxOpenTrades}</Badge>}
      />
    </section>
  );
}

async function DashboardMarketFocusSection() {
  const signals = await getRecentSignals();
  const activeSignals = signals.filter((signal) => signal.eligible).slice(0, 5);
  const activeSymbol = activeSignals[0]?.symbol ?? "BTCUSDT";
  const chartSymbol = process.env.NEXT_PUBLIC_DEFAULT_CHART_SYMBOL ?? `BYBIT:${activeSymbol}`;

  return (
    <Card>
      <CardHeader>
        <CardDescription>Live chart</CardDescription>
        <CardTitle>Market focus: {activeSymbol}</CardTitle>
      </CardHeader>
      <CardContent className="pt-2">
        <LazyTradingViewPanel symbol={chartSymbol} />
      </CardContent>
    </Card>
  );
}

async function DashboardSignalQueueSection() {
  const activeSignals = (await getRecentSignals()).filter((signal) => signal.eligible).slice(0, 5);

  return (
    <Card>
      <CardHeader>
        <CardDescription>Signal queue</CardDescription>
        <CardTitle>Highest quality active reads</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {activeSignals.length > 0 ? (
          activeSignals.map((signal) => (
            <div key={`${signal.symbol}-${signal.interval}-${signal.timestamp}`} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm text-white/55">{signal.interval}</p>
                  <h3 className="text-lg font-semibold">{signal.symbol}</h3>
                </div>
                <Badge variant={signal.action === "BUY" ? "positive" : signal.action === "SELL" ? "negative" : "neutral"}>
                  {signal.action}
                </Badge>
              </div>
              <div className="mt-4 flex items-center gap-2 text-sm text-white/65">
                <ShieldCheck className="h-4 w-4 text-cyan-200" />
                <span>Confidence {signal.confidence}%</span>
              </div>
              <p className="mt-3 whitespace-pre-line text-sm leading-6 text-white/65">{signal.reason}</p>
            </div>
          ))
        ) : (
          <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-6 text-sm leading-6 text-white/60">
            No active eligible signals yet. The engine is still logging setups, but nothing has cleared the current confidence and risk filters.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

async function DashboardPerformanceSection({ activeMode }: { activeMode: "paper" | "live" }) {
  const performanceLogs = await getRecentPerformanceLogs(activeMode);
  const latestPerformance = performanceLogs[0];

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardDescription>Equity curve</CardDescription>
          <CardTitle>{activeMode === "paper" ? "Paper rhythm" : "Live rhythm"}</CardTitle>
        </div>
        <Badge variant="accent">
          <Sparkles className="mr-1 h-3.5 w-3.5" />
          {activeMode.toUpperCase()} win rate {latestPerformance?.win_rate ?? 0}%
        </Badge>
      </CardHeader>
      <CardContent className="pt-2">
        <PerformanceChart logs={performanceLogs} />
      </CardContent>
    </Card>
  );
}

async function DashboardTradesSection({ activeMode }: { activeMode: "paper" | "live" }) {
  const trades = (await getRecentTrades(activeMode)).filter((trade) => trade.status !== "PENDING_APPROVAL");

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardDescription>Recent executions</CardDescription>
          <CardTitle>{activeMode === "paper" ? "Paper history" : "Live history"}</CardTitle>
        </div>
        <Badge variant="neutral">
          <ArrowUpRight className="mr-1 h-3.5 w-3.5" />
          {activeMode.toUpperCase()} lane
        </Badge>
      </CardHeader>
      <CardContent className="pt-2">
        <TradesTable trades={trades.slice(0, 6)} />
      </CardContent>
    </Card>
  );
}

async function DashboardSignalsLedgerSection() {
  const signals = await getRecentSignals();

  return (
    <Card>
      <CardHeader>
        <CardDescription>Signal ledger</CardDescription>
        <CardTitle>Latest model output</CardTitle>
      </CardHeader>
      <CardContent className="pt-2">
        <SignalsTable signals={signals} />
      </CardContent>
    </Card>
  );
}

function MetricsSectionSkeleton() {
  return (
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className="rounded-[28px] border border-white/10 bg-white/[0.03] p-5">
          <div className="h-4 w-24 animate-pulse rounded bg-white/[0.05]" />
          <div className="mt-5 h-9 w-32 animate-pulse rounded bg-white/[0.08]" />
          <div className="mt-5 h-3 w-full animate-pulse rounded bg-white/[0.05]" />
          <div className="mt-2 h-3 w-5/6 animate-pulse rounded bg-white/[0.05]" />
          <div className="mt-5 h-7 w-28 animate-pulse rounded-full bg-white/[0.06]" />
        </div>
      ))}
    </section>
  );
}

function PanelSkeleton({ className }: { className: string }) {
  return (
    <div className={`rounded-[28px] border border-white/10 bg-white/[0.03] p-6 ${className}`}>
      <div className="h-4 w-28 animate-pulse rounded bg-white/[0.05]" />
      <div className="mt-4 h-7 w-56 animate-pulse rounded bg-white/[0.08]" />
      <div className="mt-8 h-[calc(100%-4.5rem)] animate-pulse rounded-[22px] bg-white/[0.04]" />
    </div>
  );
}
