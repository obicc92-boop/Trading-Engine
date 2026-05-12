import type { ReactNode } from "react";
import { Activity, BrainCircuit, ShieldCheck, Timer, TrendingUp } from "lucide-react";

import { LazyTradingViewPanel } from "@/components/lazy-tradingview-panel";
import { MetricCard } from "@/components/metric-card";
import { ModeTabs } from "@/components/mode-tabs";
import { PositionsRefresher } from "@/components/positions-refresher";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getOpenTradeMonitor } from "@/lib/data";
import { formatCompactDate, formatCurrency, formatPercent, formatSignedCurrency } from "@/lib/utils";


type PageProps = {
  searchParams?: Promise<{
    mode?: string;
  }>;
};

export default async function PositionsPage({ searchParams }: PageProps) {
  const params = (await searchParams) ?? {};
  const activeMode = params.mode === "live" ? "live" : "paper";
  const monitor = await getOpenTradeMonitor(activeMode);
  const focusTrade = monitor.entries[0];
  const chartSymbol = focusTrade
    ? `BYBIT:${focusTrade.trade.symbol}`
    : process.env.NEXT_PUBLIC_DEFAULT_CHART_SYMBOL ?? "BYBIT:BTCUSDT";

  return (
    <div className="space-y-6">
      <PositionsRefresher />
      <section className="flex flex-col gap-4 rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Trade monitor</p>
          <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl lg:text-4xl">Watch the {activeMode} lane while trades are alive, not just after they close.</h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
            This page shows current mark-to-market, stop and target distance, time in trade, last signal context, and the most recent coach note tied to each {activeMode} position.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <ModeTabs pathname="/positions" activeMode={activeMode} />
          <Badge variant="accent">{monitor.totalOpenTrades} open trade{monitor.totalOpenTrades === 1 ? "" : "s"}</Badge>
          <Badge variant={monitor.totalUnrealizedPnl >= 0 ? "positive" : "negative"}>{formatSignedCurrency(monitor.totalUnrealizedPnl)} unrealized</Badge>
          <Badge variant="neutral">{monitor.lastUpdated ? `Updated ${formatCompactDate(monitor.lastUpdated)}` : "Waiting for data"}</Badge>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          title="Total Open Trades"
          value={String(monitor.totalOpenTrades)}
          description={`${activeMode === "paper" ? "Paper" : "Live"} positions currently being managed by the engine.`}
          footnote={<Badge variant="neutral">Live via Realtime</Badge>}
        />
        <MetricCard
          title="Total Unrealized PnL"
          value={formatSignedCurrency(monitor.totalUnrealizedPnl)}
          description={`Combined mark-to-market result across open ${activeMode} positions.`}
          footnote={<Badge variant={monitor.totalUnrealizedPnl >= 0 ? "positive" : "negative"}>Live-ish view</Badge>}
        />
        <MetricCard
          title="Average Open Return"
          value={formatPercent(monitor.averageUnrealizedPct)}
          description="Average open-trade percentage return right now."
          footnote={<Badge variant={monitor.averageUnrealizedPct >= 0 ? "positive" : "negative"}>{monitor.highestConvictionLabel}</Badge>}
        />
        <MetricCard
          title="Nearest Stop"
          value={monitor.nearestStopLabel}
          description="The open trade currently closest to its stop level."
          footnote={<Badge variant="neutral">Watch this one first</Badge>}
        />
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <Card>
          <CardHeader>
            <CardDescription>Chart focus</CardDescription>
            <CardTitle>{focusTrade ? `Market focus: ${focusTrade.trade.symbol}` : "Market focus"}</CardTitle>
          </CardHeader>
          <CardContent className="pt-2">
            <LazyTradingViewPanel symbol={chartSymbol} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>How to use this view</CardDescription>
            <CardTitle>Monitor without micromanaging</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 pt-2">
            <MonitorHint
              icon={<ShieldCheck className="h-4 w-4 text-cyan-200" />}
              title="Start with stop distance"
              body="If one position is much closer to its stop than the others, that is the first trade to inspect emotionally and technically."
            />
            <MonitorHint
              icon={<TrendingUp className="h-4 w-4 text-emerald-200" />}
              title="Read unrealized PnL as context, not validation"
              body="A green open trade is not automatically a good trade, and a red one is not automatically a bad trade. Use the signal and regime context next to it."
            />
            <MonitorHint
              icon={<BrainCircuit className="h-4 w-4 text-cyan-200" />}
              title="Coach note stays attached"
              body="If a trade opened with a coach note, the note is shown beside it so you can compare the original reasoning with what the market is doing now."
            />
            <MonitorHint
              icon={<Timer className="h-4 w-4 text-amber-200" />}
              title="Time in trade matters"
              body="A setup that drifts too long without progressing may deserve more scrutiny than a fresh trade with the same PnL."
            />
          </CardContent>
        </Card>
      </section>

      {monitor.entries.length > 0 ? (
        <section className="grid gap-6 xl:grid-cols-2">
          {monitor.entries.map((entry) => (
            <Card key={`${entry.trade.id ?? entry.trade.symbol}-${entry.trade.opened_at}`}>
              <CardHeader className="gap-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <CardDescription>{entry.trade.interval}</CardDescription>
                    <CardTitle>{entry.trade.symbol}</CardTitle>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="accent">{entry.trade.status}</Badge>
                    <Badge variant={entry.unrealizedPnl >= 0 ? "positive" : "negative"}>{formatSignedCurrency(entry.unrealizedPnl)}</Badge>
                    <Badge variant="neutral">{entry.regimeLabel}</Badge>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-5 pt-0">
                <div className="grid gap-4 sm:grid-cols-2">
                  <StatPanel label="Current Price" value={formatCurrency(entry.currentPrice)} />
                  <StatPanel label="Open Return" value={formatPercent(entry.unrealizedPct)} tone={entry.unrealizedPct >= 0 ? "positive" : "negative"} />
                  <StatPanel label="Entry" value={formatCurrency(entry.trade.entry)} />
                  <StatPanel label="Duration" value={formatDuration(entry.durationHours)} />
                </div>

                <div className="space-y-3">
                  <ProgressRow
                    label="Target progress"
                    value={entry.targetProgressPct}
                    helper={
                      entry.distanceToTargetPct != null
                        ? `${formatPercent(Math.max(entry.distanceToTargetPct, 0))} from target`
                        : "No target distance available"
                    }
                  />
                  <ProgressRow
                    label="Stop buffer"
                    value={entry.distanceToStopPct != null ? clampPercent(entry.distanceToStopPct * 4) : null}
                    helper={
                      entry.distanceToStopPct != null
                        ? `${formatPercent(entry.distanceToStopPct)} above stop`
                        : "No stop distance available"
                    }
                    negative
                  />
                </div>

                <div className="grid gap-4 sm:grid-cols-3">
                  <StatPanel label="Stop Loss" value={formatCurrency(entry.trade.stop_loss)} />
                  <StatPanel label="Take Profit" value={formatCurrency(entry.trade.take_profit)} />
                  <StatPanel label="Position Size" value={String(entry.trade.position_size)} />
                </div>

                <div className="rounded-2xl border border-amber-300/15 bg-amber-300/[0.05] p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-white">Execution drag</p>
                      <p className="mt-1 text-xs text-white/55">Net unrealized PnL already includes the entry fee and an estimated exit fee.</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="neutral">{entry.simulationSlippageBps.toFixed(2)} bps slippage</Badge>
                      <Badge variant="neutral">{formatPercent(entry.totalFeesIfClosedNow > 0 && entry.trade.notional_value ? (entry.totalFeesIfClosedNow / Number(entry.trade.notional_value)) * 100 : 0)} fee drag</Badge>
                      {entry.sizeCappedForCash ? <Badge variant="negative">Size trimmed for cash</Badge> : null}
                    </div>
                  </div>
                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    <StatPanel label="Raw Signal Entry" value={entry.entryPriceRaw != null ? formatCurrency(entry.entryPriceRaw) : "Not logged"} />
                    <StatPanel label="Filled Entry" value={formatCurrency(entry.trade.entry)} />
                    <StatPanel label="Entry Fee Paid" value={formatSignedCurrency(-entry.entryFeePaid)} tone="negative" />
                    <StatPanel label="Exit Fee If Closed Now" value={formatSignedCurrency(-entry.estimatedExitFee)} tone="negative" />
                  </div>
                  <p className="mt-4 text-xs text-white/50">
                    Entry slippage drag: {formatSignedCurrency(-calculateEntrySlippageCost(entry.entryPriceRaw, entry.trade.entry, entry.trade.position_size))}
                  </p>
                </div>

                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <Activity className="h-4 w-4 text-cyan-200" />
                      <p className="text-sm font-medium text-white">Latest signal context</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {entry.lastSignalAction ? <Badge variant={entry.lastSignalAction === "BUY" ? "positive" : entry.lastSignalAction === "SELL" ? "negative" : "neutral"}>{entry.lastSignalAction}</Badge> : null}
                      {entry.lastSignalConfidence != null ? <Badge variant="neutral">{entry.lastSignalConfidence}% confidence</Badge> : null}
                    </div>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-white/72">{entry.lastSignalReason}</p>
                  <p className="mt-3 text-xs text-white/45">
                    {entry.lastSignalTimestamp ? `Signal updated ${formatCompactDate(entry.lastSignalTimestamp)}` : "No fresh signal timestamp yet."}
                  </p>
                </div>

                {entry.coachNote ? (
                  <div className="rounded-2xl border border-cyan-300/15 bg-cyan-300/[0.05] p-4">
                    <div className="flex items-center gap-2">
                      <BrainCircuit className="h-4 w-4 text-cyan-200" />
                      <p className="text-sm font-medium text-white">{entry.coachNote.title}</p>
                    </div>
                    <p className="mt-3 text-sm leading-6 text-white/72">{summarizeCoachNote(entry.coachNote.body)}</p>
                    <p className="mt-3 text-xs text-white/45">Saved {formatCompactDate(entry.coachNote.timestamp)}</p>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </section>
      ) : (
        <Card>
          <CardHeader>
            <CardDescription>No live paper positions</CardDescription>
            <CardTitle>Nothing is open right now</CardTitle>
          </CardHeader>
          <CardContent className="pt-2 text-sm leading-6 text-white/65">
            No {activeMode} trades are currently open. When the engine opens a new {activeMode} position, this page will show its mark-to-market progress, stop and target distance, latest signal context, and any coach note attached to it.
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function MonitorHint({
  icon,
  title,
  body
}: {
  icon: ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-center gap-3">
        {icon}
        <p className="text-sm font-medium text-white">{title}</p>
      </div>
      <p className="mt-3 text-sm leading-6 text-white/72">{body}</p>
    </div>
  );
}

function StatPanel({
  label,
  value,
  tone = "default"
}: {
  label: string;
  value: string;
  tone?: "default" | "positive" | "negative";
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
      <p className="text-xs uppercase tracking-[0.18em] text-white/45">{label}</p>
      <p
        className={`mt-3 text-xl font-semibold ${
          tone === "positive" ? "text-emerald-200" : tone === "negative" ? "text-rose-200" : "text-white"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

function ProgressRow({
  label,
  value,
  helper,
  negative = false
}: {
  label: string;
  value: number | null;
  helper: string;
  negative?: boolean;
}) {
  const progress = value == null ? 0 : clampPercent(value);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-white">{label}</p>
        <p className="text-xs text-white/45">{helper}</p>
      </div>
      <div className="h-2 rounded-full bg-white/[0.06]">
        <div
          className={`h-2 rounded-full ${negative ? "bg-rose-300/70" : "bg-emerald-300/70"}`}
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(100, value));
}

function formatDuration(hours: number): string {
  if (!Number.isFinite(hours) || hours <= 0) {
    return "0h";
  }
  if (hours >= 24) {
    return `${(hours / 24).toFixed(1)}d`;
  }
  return `${hours.toFixed(1)}h`;
}

function summarizeCoachNote(body: string): string {
  const firstParagraph = body
    .split(/\n\s*\n/)
    .map((part) => part.trim())
    .find(Boolean);

  if (!firstParagraph) {
    return "No saved coach summary.";
  }

  return firstParagraph.replace(/^Why:\s*/i, "").replace(/^Summary:\s*/i, "");
}

function calculateEntrySlippageCost(rawEntry: number | null, filledEntry: number, positionSize: number): number {
  if (rawEntry == null || !Number.isFinite(rawEntry)) {
    return 0;
  }

  return Math.max(filledEntry - rawEntry, 0) * positionSize;
}
