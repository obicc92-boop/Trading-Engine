import { ArrowLeftRight, Link2, TrendingDown, TrendingUp } from "lucide-react";

import { MetricCard } from "@/components/metric-card";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getPairedTradeComparisonSummary } from "@/lib/data";
import { formatCompactDate, formatCurrency, formatPercent, formatSignedCurrency } from "@/lib/utils";


export default async function ComparePage() {
  const summary = await getPairedTradeComparisonSummary();

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Paper vs live</p>
          <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl lg:text-4xl">Measure the gap between what the model expected and what the market actually gave you.</h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
            Each card below ties a paper trade to its live sibling through the shared trade group id saved by the backend. This is where you learn whether friction, timing, and confirmation changed the edge.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Badge variant="accent">{summary.pairedTrades} paired</Badge>
          <Badge variant="neutral">{summary.paperOnlyTrades} paper-only</Badge>
          <Badge variant="neutral">{summary.liveOnlyTrades} live-only</Badge>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard title="Tracked groups" value={String(summary.totalPairs)} description="Signals that generated at least one grouped trade record." />
        <MetricCard title="Paper net PnL" value={formatSignedCurrency(summary.paperNetPnl)} description="Net paper result across the grouped trades." />
        <MetricCard title="Live net PnL" value={formatSignedCurrency(summary.liveNetPnl)} description="Net live result across the grouped trades." />
        <MetricCard title="Live minus paper" value={formatSignedCurrency(summary.netPnlDelta)} description="Positive means live beat the paper expectation. Negative means execution drag hurt." />
      </section>

      {summary.entries.length > 0 ? (
        <section className="grid gap-6 xl:grid-cols-2">
          {summary.entries.map((entry) => (
            <Card key={entry.tradeGroupId}>
              <CardHeader className="gap-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <CardDescription>{entry.interval}</CardDescription>
                    <CardTitle>{entry.symbol}</CardTitle>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="neutral">
                      <Link2 className="mr-1 h-3.5 w-3.5" />
                      {entry.tradeGroupId.slice(0, 8)}
                    </Badge>
                    {entry.paperTrade ? <Badge variant="accent">PAPER {entry.paperStatus}</Badge> : <Badge variant="negative">No paper sibling</Badge>}
                    {entry.liveTrade ? <Badge variant="neutral">LIVE {entry.liveStatus}</Badge> : <Badge variant="negative">No live sibling</Badge>}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-5 pt-0">
                <div className="grid gap-4 sm:grid-cols-2">
                  <MetricCard title="Paper entry" value={entry.paperEntry != null ? formatCurrency(entry.paperEntry) : "Missing"} description="Expected paper fill for this grouped setup." />
                  <MetricCard title="Live entry" value={entry.liveEntry != null ? formatCurrency(entry.liveEntry) : "Missing"} description="Actual live-side tracked entry." />
                  <MetricCard title="Paper PnL" value={entry.paperPnl != null ? formatSignedCurrency(entry.paperPnl) : "Open / missing"} description="Net paper outcome so far." />
                  <MetricCard title="Live PnL" value={entry.livePnl != null ? formatSignedCurrency(entry.livePnl) : "Open / missing"} description="Net live outcome so far." />
                </div>

                <div className="grid gap-4 sm:grid-cols-3">
                  <DeltaPanel
                    title="Entry delta"
                    value={entry.entryDeltaPct}
                    helper="How far the live entry drifted from the paper entry."
                  />
                  <DeltaPanel
                    title="Exit delta"
                    value={entry.exitDeltaPct}
                    helper="How far the live exit drifted from the paper exit."
                  />
                  <DeltaPanel
                    title="Net PnL gap"
                    value={
                      entry.paperPnl != null && entry.livePnl != null
                        ? entry.livePnl - entry.paperPnl
                        : null
                    }
                    helper="Live minus paper dollars for this pair."
                    currency
                  />
                </div>

                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <div className="flex items-center gap-2">
                    <ArrowLeftRight className="h-4 w-4 text-cyan-200" />
                    <p className="text-sm font-medium text-white">Trade timing</p>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-white/72">
                    Opened {entry.openedAt ? formatCompactDate(entry.openedAt) : "Unknown"}.
                    {entry.updatedAt ? ` Last update ${formatCompactDate(entry.updatedAt)}.` : ""}
                  </p>
                  <p className="mt-3 text-xs text-white/50">
                    Paper fees: {entry.paperFees != null ? formatSignedCurrency(-entry.paperFees) : "Not logged"} | Live fees:{" "}
                    {entry.liveFees != null ? formatSignedCurrency(-entry.liveFees) : "Not yet separated"}
                  </p>
                </div>

                {entry.coachNotes.length > 0 ? (
                  <div className="rounded-2xl border border-cyan-300/15 bg-cyan-300/[0.05] p-4">
                    <p className="text-sm font-medium text-white">Attached coach notes</p>
                    <div className="mt-3 space-y-3">
                      {entry.coachNotes.slice(0, 2).map((note) => (
                        <div key={`${note.id ?? note.timestamp}-${note.title}`} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
                          <p className="text-sm font-medium text-white">{note.title}</p>
                          <p className="mt-2 text-sm leading-6 text-white/68">{summarizeCoachBody(note.body)}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </section>
      ) : (
        <Card>
          <CardHeader>
            <CardDescription>No paired trade groups yet</CardDescription>
            <CardTitle>Nothing to compare yet</CardTitle>
          </CardHeader>
          <CardContent className="pt-2 text-sm leading-6 text-white/65">
            Once the backend starts writing paper and live siblings with a shared trade group id, this page will compare the two lanes side by side.
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function DeltaPanel({
  title,
  value,
  helper,
  currency = false
}: {
  title: string;
  value: number | null;
  helper: string;
  currency?: boolean;
}) {
  const tone = value == null ? "neutral" : value >= 0 ? "positive" : "negative";
  const formattedValue = value == null ? "Not enough data" : currency ? formatSignedCurrency(value) : formatPercent(value);

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-center gap-2">
        {tone === "neutral" ? (
          <ArrowLeftRight className="h-4 w-4 text-cyan-200" />
        ) : tone === "positive" ? (
          <TrendingUp className="h-4 w-4 text-emerald-200" />
        ) : (
          <TrendingDown className="h-4 w-4 text-rose-200" />
        )}
        <p className="text-sm font-medium text-white">{title}</p>
      </div>
      <p className={`mt-3 text-xl font-semibold ${tone === "positive" ? "text-emerald-200" : tone === "negative" ? "text-rose-200" : "text-white"}`}>
        {formattedValue}
      </p>
      <p className="mt-2 text-xs leading-5 text-white/45">{helper}</p>
    </div>
  );
}

function summarizeCoachBody(body: string): string {
  const firstParagraph = body
    .split(/\n\s*\n/)
    .map((part) => part.trim())
    .find(Boolean);

  return firstParagraph ?? body.trim();
}
