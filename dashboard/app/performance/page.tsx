import Link from "next/link";

import { MetricCard } from "@/components/metric-card";
import { ModeTabs } from "@/components/mode-tabs";
import { PerformanceChart } from "@/components/performance-chart";
import { TradesTable } from "@/components/trades-table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getPairedTradeComparisonSummary,
  getPaperReviewSummary,
  getRecentPerformanceLogs,
  getRecentTrades
} from "@/lib/data";
import { formatCurrency, formatPercent, formatSignedCurrency } from "@/lib/utils";


type PageProps = {
  searchParams?: Promise<{
    mode?: string;
  }>;
};

export default async function PerformancePage({ searchParams }: PageProps) {
  const params = (await searchParams) ?? {};
  const activeMode = params.mode === "live" ? "live" : "paper";

  const [performanceLogs, trades, paperSummary, comparisonSummary] = await Promise.all([
    getRecentPerformanceLogs(activeMode),
    getRecentTrades(activeMode),
    getPaperReviewSummary(),
    getPairedTradeComparisonSummary()
  ]);
  const latest = performanceLogs[0];
  const actionableTrades = trades.filter((trade) => trade.status !== "PENDING_APPROVAL");
  const closedTrades = actionableTrades.filter((trade) => trade.status === "CLOSED");

  return (
    <div className="space-y-6">
      <section className="rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Performance</p>
            <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl">See the paper edge and the live reality separately.</h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
              Paper mode tells you whether the rules have an edge. Live mode tells you how much execution, confirmation, and exchange friction changed the real outcome.
            </p>
          </div>
          <ModeTabs pathname="/performance" activeMode={activeMode} />
        </div>
      </section>

      {activeMode === "paper" ? (
        <>
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard title="Total Realized PnL" value={formatSignedCurrency(paperSummary.totalRealizedPnl)} description="Closed performance across recent paper trades." />
            <MetricCard title="Expectancy / Trade" value={formatSignedCurrency(paperSummary.expectancy)} description="Average dollars earned or lost per completed paper trade." />
            <MetricCard
              title="Profit Factor"
              value={paperSummary.profitFactor == null ? "Not enough data" : Number.isFinite(paperSummary.profitFactor) ? paperSummary.profitFactor.toFixed(2) : "Clean so far"}
              description="Gross wins divided by gross losses. This matters more than win rate alone."
            />
            <MetricCard title="Max Drawdown" value={formatSignedCurrency(-paperSummary.maxDrawdownValue)} description="Largest peak-to-trough equity drop in tracked paper history." />
          </section>

          <Card>
            <CardHeader>
              <CardDescription>Review focus</CardDescription>
              <CardTitle>What deserves your attention next</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 pt-2">
              {paperSummary.reviewPrompts.map((prompt) => (
                <div key={prompt} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm leading-6 text-white/72">
                  {prompt}
                </div>
              ))}
            </CardContent>
          </Card>
        </>
      ) : (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard title="Live Equity" value={formatCurrency(latest?.total_equity ?? 0)} description="Latest exchange-backed portfolio value captured by the worker." />
          <MetricCard title="Live Available Cash" value={formatCurrency(latest?.available_cash ?? 0)} description="Capital the live lane reported as available for fresh entries." />
          <MetricCard title="Live Open Positions" value={String(latest?.open_positions ?? 0)} description="Current live exposure being tracked in the database." />
          <MetricCard title="Live Win Rate" value={formatPercent(latest?.win_rate ?? 0)} description="Directional context only. Use the compare page to judge slippage and fill quality." />
        </section>
      )}

      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardDescription>{activeMode === "paper" ? "Equity evolution" : "Lane snapshot"}</CardDescription>
            <CardTitle>{activeMode === "paper" ? "Paper account curve" : "Live lane curve"}</CardTitle>
          </div>
          <Badge variant={activeMode === "paper" ? "accent" : "neutral"}>{activeMode.toUpperCase()} lane</Badge>
        </CardHeader>
        <CardContent className="pt-2">
          <PerformanceChart logs={performanceLogs} />
        </CardContent>
      </Card>

      <section className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader>
            <CardDescription>Lane comparison</CardDescription>
            <CardTitle>Paper versus live drift</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 pt-2">
            <div className="grid gap-4 sm:grid-cols-3">
              <MetricCard title="Paired trades" value={String(comparisonSummary.pairedTrades)} description="Signals that produced both a paper trade and a live sibling." />
              <MetricCard title="Paper net PnL" value={formatSignedCurrency(comparisonSummary.paperNetPnl)} description="Net result from the paired paper side." />
              <MetricCard title="Live minus paper" value={formatSignedCurrency(comparisonSummary.netPnlDelta)} description="Reality gap across the paired executions." />
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm leading-6 text-white/72">
              The paired compare view shows where live fills, timing, and confirmations helped or hurt relative to the paper expectation.
            </div>
            <Link href="/compare" className="inline-flex rounded-full border border-cyan-300/20 bg-cyan-300/12 px-4 py-2 text-sm text-white transition hover:bg-cyan-300/18">
              Open paper vs live compare
            </Link>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>Current posture</CardDescription>
            <CardTitle>Open risk and closed results</CardTitle>
          </CardHeader>
          <CardContent className="pt-2">
            <div className="mb-4 grid gap-4 md:grid-cols-3">
              <MetricCard
                title="Unrealized PnL"
                value={formatCurrency(latest?.unrealized_pnl ?? 0)}
                description={activeMode === "paper" ? "Current mark-to-market effect from open paper positions." : "Current live-lane unrealized field captured by the worker."}
              />
              <MetricCard
                title="Latest Win Rate"
                value={formatPercent((activeMode === "paper" ? paperSummary.winRate : latest?.win_rate) ?? 0)}
                description={activeMode === "paper" ? "Helpful context, but never enough on its own." : "Live lane hit-rate snapshot from recent closed trades."}
              />
              <MetricCard
                title="Closed Trades"
                value={String(closedTrades.length)}
                description={activeMode === "paper" ? "Recent paper trade outcomes." : "Recent live trade outcomes stored in Supabase."}
              />
            </div>
            <TradesTable trades={closedTrades.length > 0 ? closedTrades : actionableTrades} />
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
