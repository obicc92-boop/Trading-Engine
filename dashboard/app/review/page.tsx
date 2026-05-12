import type { ReactNode } from "react";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, BrainCircuit, Camera, ShieldCheck, Sparkles } from "lucide-react";

import { ReviewNoteForm } from "@/components/review-note-form";
import { JournalFeed } from "@/components/journal-feed";
import { MetricCard } from "@/components/metric-card";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { getPaperReviewSummary, getPaperReviewTrades } from "@/lib/data";
import type { ReviewBreakdownRow, ReviewPeriodSummary } from "@/lib/types";
import { formatCompactDate, formatCurrency, formatHours, formatPercent, formatSignedCurrency } from "@/lib/utils";


export default async function ReviewPage() {
  const [summary, paperTrades] = await Promise.all([getPaperReviewSummary(), getPaperReviewTrades()]);
  const strongestSymbols = summary.symbolBreakdown.slice(0, 4);
  const weakestSymbols = [...summary.symbolBreakdown].sort((left, right) => left.totalPnl - right.totalPnl).slice(0, 4);
  const strongestIntervals = summary.intervalBreakdown.slice(0, 4);
  const weakestIntervals = [...summary.intervalBreakdown].sort((left, right) => left.totalPnl - right.totalPnl).slice(0, 4);
  const strongestSetups = summary.setupBreakdown.slice(0, 4);
  const weakestSetups = [...summary.setupBreakdown].sort((left, right) => left.totalPnl - right.totalPnl).slice(0, 4);
  const tradeOptions = paperTrades
    .filter((trade) => trade.status === "CLOSED" || trade.status === "OPEN")
    .slice(0, 20);
  const dominantRegime = summary.regimeBreakdown[0];

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Review lab</p>
          <h2 className="mt-2 text-2xl font-semibold sm:text-3xl lg:text-4xl">Turn paper trades into a study system, not just a scorecard.</h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
            This page is your trading classroom. It compares recent weeks, highlights the best and worst lessons, tracks which market regimes are paying or hurting, and gives you a place to save replay notes with chart links.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Badge variant="accent">{formatPercent(summary.approvalRate)} approved</Badge>
          <Badge variant="neutral">{summary.closedTrades} closed paper trades</Badge>
          <Badge variant="positive">{summary.recentReviewNotes.length} recent review notes</Badge>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <MetricCard
          title="Total Realized PnL"
          value={formatSignedCurrency(summary.totalRealizedPnl)}
          description="Closed paper-trade result across the review window."
          footnote={<Badge variant={summary.totalRealizedPnl >= 0 ? "positive" : "negative"}>{summary.closedTrades} closed trades</Badge>}
        />
        <MetricCard
          title="Expectancy / Trade"
          value={formatSignedCurrency(summary.expectancy)}
          description="Average dollars earned or lost per completed paper trade."
          footnote={<Badge variant={summary.expectancy >= 0 ? "positive" : "negative"}>Win rate {formatPercent(summary.winRate)}</Badge>}
        />
        <MetricCard
          title="Profit Factor"
          value={formatProfitFactor(summary.profitFactor)}
          description="Gross wins divided by gross losses. Higher than 1.00 means gains are outpacing losses."
          footnote={
            <Badge variant={summary.profitFactor == null ? "neutral" : summary.profitFactor >= 1 ? "positive" : "negative"}>
              Avg win {formatCurrency(summary.averageWin)}
            </Badge>
          }
        />
        <MetricCard
          title="Max Drawdown"
          value={formatSignedCurrency(-summary.maxDrawdownValue)}
          description="Largest peak-to-trough paper equity drop in the tracked history."
          footnote={<Badge variant={summary.maxDrawdownPct < 3 ? "positive" : "negative"}>{formatPercent(summary.maxDrawdownPct)} drawdown</Badge>}
        />
        <MetricCard
          title="Average Hold"
          value={formatHours(summary.averageHoldHours)}
          description="Typical time a paper trade stays open before it resolves."
          footnote={<Badge variant="neutral">{summary.currentStreakLabel}</Badge>}
        />
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <Card>
          <CardHeader>
            <CardDescription>Weekly summary</CardDescription>
            <CardTitle>This week versus last week</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 pt-2 lg:grid-cols-2">
            <PeriodCard
              period={summary.weeklyComparison.current}
              tone="current"
              pnlDelta={summary.weeklyComparison.current.totalPnl - summary.weeklyComparison.previous.totalPnl}
              expectancyDelta={summary.weeklyComparison.current.expectancy - summary.weeklyComparison.previous.expectancy}
            />
            <PeriodCard period={summary.weeklyComparison.previous} tone="previous" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>Auto-highlights</CardDescription>
            <CardTitle>Best lesson and biggest warning</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 pt-2">
            {summary.bestSetup ? (
              <HighlightCard
                icon={<Sparkles className="h-4 w-4 text-emerald-200" />}
                title={summary.bestSetup.title}
                headline={summary.bestSetup.headline}
                detail={summary.bestSetup.detail}
                regimeLabel={summary.bestSetup.regimeLabel}
                pnl={summary.bestSetup.pnl}
                positive
              />
            ) : null}
            {summary.worstMistake ? (
              <HighlightCard
                icon={<AlertTriangle className="h-4 w-4 text-rose-200" />}
                title={summary.worstMistake.title}
                headline={summary.worstMistake.headline}
                detail={summary.worstMistake.detail}
                regimeLabel={summary.worstMistake.regimeLabel}
                pnl={summary.worstMistake.pnl}
              />
            ) : null}
            {dominantRegime ? (
              <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-white/45">Dominant recent regime</p>
                <div className="mt-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-lg font-semibold text-white">{dominantRegime.label}</p>
                    <p className="mt-1 text-sm text-white/55">{dominantRegime.trades} closed trade{dominantRegime.trades === 1 ? "" : "s"}</p>
                  </div>
                  <Badge variant={dominantRegime.totalPnl >= 0 ? "positive" : "negative"}>
                    {formatSignedCurrency(dominantRegime.totalPnl)}
                  </Badge>
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader>
            <CardDescription>Where performance is clustering</CardDescription>
            <CardTitle>Symbols, intervals, setups, and selection quality</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-6 pt-2 lg:grid-cols-2">
            <BreakdownPanel title="Strongest symbols" subtitle="Pairs carrying the current paper results." rows={strongestSymbols} emptyLabel="No closed paper trades yet." />
            <BreakdownPanel title="Weakest symbols" subtitle="The first place to look for repeated mistakes." rows={weakestSymbols} emptyLabel="No weak symbol cluster yet." />
            <BreakdownPanel title="Strongest intervals" subtitle="Timeframes with the cleanest recent paper outcome." rows={strongestIntervals} emptyLabel="No interval data yet." />
            <BreakdownPanel title="Weakest intervals" subtitle="If one timeframe keeps leaking, tune there first." rows={weakestIntervals} emptyLabel="No interval data yet." />
            <BreakdownPanel title="Strongest setups" subtitle="Which entry pattern is doing the heavy lifting." rows={strongestSetups} emptyLabel="No setup-type data yet." />
            <BreakdownPanel title="Weakest setups" subtitle="Where breakout or pullback logic may need retuning." rows={weakestSetups} emptyLabel="No setup-type data yet." />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>Regime map and selection filter</CardDescription>
            <CardTitle>What market and ranking context is paying you</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6 pt-2">
            {summary.regimeBreakdown.length > 0 ? (
              <div className="space-y-3">
                {summary.regimeBreakdown.map((row) => (
                  <div key={row.label} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium text-white">{row.label}</p>
                        <p className="mt-1 text-xs text-white/50">
                          {row.trades} trade{row.trades === 1 ? "" : "s"} | {formatPercent(row.winRate)} win rate
                        </p>
                      </div>
                      <Badge variant={row.totalPnl >= 0 ? "positive" : "negative"}>{formatSignedCurrency(row.totalPnl)}</Badge>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-5 text-sm leading-6 text-white/60">
                Regime tagging will show up here as more paper trades resolve with indicator context.
              </div>
            )}

            <div>
              <p className="text-sm font-medium text-white/80">Selection quality</p>
              <p className="mt-1 text-xs text-white/50">How the top-ranked-symbol filter is affecting results.</p>
              <div className="mt-3 space-y-3">
                {summary.selectionBreakdown.length > 0 ? (
                  summary.selectionBreakdown.map((row) => (
                    <div key={row.label} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="text-sm font-medium text-white">{row.label}</p>
                          <p className="mt-1 text-xs text-white/50">
                            {row.trades} trade{row.trades === 1 ? "" : "s"} | {formatPercent(row.winRate)} win rate
                          </p>
                        </div>
                        <Badge variant={row.totalPnl >= 0 ? "positive" : "negative"}>{formatSignedCurrency(row.totalPnl)}</Badge>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-5 text-sm leading-6 text-white/60">
                    Selection-filter data will appear here once more closed trades carry the new ranking metadata.
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader>
            <CardDescription>Replay these first</CardDescription>
            <CardTitle>Trades worth inspecting next</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 pt-2">
            {summary.flaggedTrades.length > 0 ? (
              summary.flaggedTrades.map((trade) => (
                <div key={`${trade.id ?? trade.symbol}-${trade.closedAt ?? trade.openedAt}`} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm text-white/55">{trade.interval}</p>
                      <h3 className="text-lg font-semibold text-white">{trade.symbol}</h3>
                    </div>
                    <Badge variant={trade.pnl >= 0 ? "positive" : "negative"}>{formatSignedCurrency(trade.pnl)}</Badge>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge variant="accent">{trade.setupType}</Badge>
                    <Badge variant="neutral">{trade.exitLabel}</Badge>
                    <Badge variant={trade.pnlPct >= 0 ? "positive" : "negative"}>{formatPercent(trade.pnlPct)}</Badge>
                    {trade.symbolRank != null ? <Badge variant="neutral">Rank #{trade.symbolRank}</Badge> : null}
                    <Badge variant={trade.tradePriorityAllowed ? "positive" : "negative"}>
                      {trade.tradePriorityAllowed ? "Tradable rank" : "Watch-only rank"}
                    </Badge>
                    {trade.sizeCappedForCash ? <Badge variant="negative">Size trimmed for cash</Badge> : null}
                  </div>
                  <p className="mt-3 text-xs text-white/45">Strength score: {trade.symbolStrengthScore.toFixed(1)}</p>
                  <div className="mt-4 rounded-2xl border border-amber-300/15 bg-amber-300/[0.05] p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <p className="text-sm font-medium text-white">Execution drag</p>
                      <Badge variant="neutral">{trade.slippageBps.toFixed(2)} bps slippage model</Badge>
                    </div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                      <DragStat
                        label="Raw / filled entry"
                        value={
                          trade.entryPriceRaw != null && trade.entryPriceFilled != null
                            ? `${formatCurrency(trade.entryPriceRaw)} -> ${formatCurrency(trade.entryPriceFilled)}`
                            : "Not logged"
                        }
                      />
                      <DragStat
                        label="Raw / filled exit"
                        value={
                          trade.exitPriceRaw != null && trade.exitPriceFilled != null
                            ? `${formatCurrency(trade.exitPriceRaw)} -> ${formatCurrency(trade.exitPriceFilled)}`
                            : "Not logged"
                        }
                      />
                      <DragStat label="Entry fee" value={formatSignedCurrency(-trade.entryFeePaid)} negative />
                      <DragStat label="Exit fee" value={formatSignedCurrency(-trade.exitFeePaid)} negative />
                    </div>
                    <p className="mt-4 text-xs text-white/50">
                      Total fees paid: {formatSignedCurrency(-trade.totalFeesPaid)} | Entry slippage drag: {formatSignedCurrency(-calculateReviewSlippageCost(trade.entryPriceRaw, trade.entryPriceFilled, trade.positionSize))}
                      {trade.exitPriceRaw != null && trade.exitPriceFilled != null
                        ? ` | Exit slippage drag: ${formatSignedCurrency(-calculateReviewExitSlippageCost(trade.exitPriceRaw, trade.exitPriceFilled, trade.positionSize))}`
                        : ""}
                    </p>
                  </div>
                  <p className="mt-4 text-sm leading-6 text-white/72">{trade.whyItMatters}</p>
                  <p className="mt-3 text-xs leading-5 text-white/45">{trade.note}</p>
                  <p className="mt-3 text-xs text-white/45">
                    Closed {trade.closedAt ? formatCompactDate(trade.closedAt) : formatCompactDate(trade.openedAt)}
                  </p>
                </div>
              ))
            ) : (
              <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-5 text-sm leading-6 text-white/60">
                There are no closed paper trades to replay yet. Let the engine collect more data and come back after a few completed setups.
              </div>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-6">
          <Card>
            <CardHeader>
              <CardDescription>Coaching prompts</CardDescription>
              <CardTitle>What to review next</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 pt-2">
              {summary.reviewPrompts.map((prompt) => (
                <div key={prompt} className="flex gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-cyan-200" />
                  <p className="text-sm leading-6 text-white/72">{prompt}</p>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardDescription>Discipline footprint</CardDescription>
              <CardTitle>Journal and coach activity</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 pt-2">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <div className="flex items-center gap-2">
                    <BrainCircuit className="h-4 w-4 text-cyan-200" />
                    <p className="text-xs uppercase tracking-[0.18em] text-white/45">Coach notes</p>
                  </div>
                  <p className="mt-3 text-2xl font-semibold text-white">{summary.coachNotes}</p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <div className="flex items-center gap-2">
                    <Camera className="h-4 w-4 text-amber-200" />
                    <p className="text-xs uppercase tracking-[0.18em] text-white/45">Review notes</p>
                  </div>
                  <p className="mt-3 text-2xl font-semibold text-white">{summary.recentReviewNotes.length}</p>
                </div>
              </div>

              {summary.journalTagBreakdown.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {summary.journalTagBreakdown.map((row) => (
                    <Badge key={row.tag} variant="neutral">
                      {row.tag} x{row.count}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="text-sm leading-6 text-white/60">
                  The journal does not have many meaningful review tags yet. As more paper trades resolve, this area will show where your process notes are concentrating.
                </p>
              )}

              {summary.lastUpdated ? (
                <p className="text-xs text-white/45">Last synced from recent paper data: {formatCompactDate(summary.lastUpdated)}</p>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
        <Card>
          <CardHeader>
            <CardDescription>Note workshop</CardDescription>
            <CardTitle>Save what the replay taught you</CardTitle>
          </CardHeader>
          <CardContent className="pt-2">
            <ReviewNoteForm trades={tradeOptions} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>Recent manual notes</CardDescription>
            <CardTitle>Your saved replay archive</CardTitle>
          </CardHeader>
          <CardContent className="pt-2">
            {summary.recentReviewNotes.length > 0 ? (
              <JournalFeed entries={summary.recentReviewNotes} />
            ) : (
              <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-5 text-sm leading-6 text-white/60">
                No manual review notes yet. Save one after a replay and it will show up here with your chart links.
              </div>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

function PeriodCard({
  period,
  tone,
  pnlDelta,
  expectancyDelta
}: {
  period: ReviewPeriodSummary;
  tone: "current" | "previous";
  pnlDelta?: number;
  expectancyDelta?: number;
}) {
  return (
    <div className="rounded-[24px] border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-white/45">{period.label}</p>
          <p className="mt-1 text-sm text-white/55">{formatWeekRange(period.startDate, period.endDate)}</p>
        </div>
        <Badge variant={tone === "current" ? "accent" : "neutral"}>{period.closedTrades} trades</Badge>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-white/45">PnL</p>
          <p className={`mt-2 text-2xl font-semibold ${period.totalPnl >= 0 ? "text-emerald-200" : "text-rose-200"}`}>{formatSignedCurrency(period.totalPnl)}</p>
          {typeof pnlDelta === "number" ? (
            <p className={`mt-1 text-xs ${pnlDelta >= 0 ? "text-emerald-200" : "text-rose-200"}`}>vs last week {formatSignedCurrency(pnlDelta)}</p>
          ) : null}
        </div>
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-white/45">Expectancy</p>
          <p className={`mt-2 text-2xl font-semibold ${period.expectancy >= 0 ? "text-emerald-200" : "text-rose-200"}`}>{formatSignedCurrency(period.expectancy)}</p>
          {typeof expectancyDelta === "number" ? (
            <p className={`mt-1 text-xs ${expectancyDelta >= 0 ? "text-emerald-200" : "text-rose-200"}`}>delta {formatSignedCurrency(expectancyDelta)}</p>
          ) : null}
        </div>
      </div>

      <div className="mt-4 grid gap-2 text-sm text-white/65">
        <p>Win rate: {formatPercent(period.winRate)}</p>
        <p>Approved signals: {period.approvedSignals}/{period.totalSignals || 0}</p>
        <p>Drawdown: {formatPercent(period.maxDrawdownPct)}</p>
      </div>
    </div>
  );
}

function HighlightCard({
  icon,
  title,
  headline,
  detail,
  regimeLabel,
  pnl,
  positive = false
}: {
  icon: ReactNode;
  title: string;
  headline: string;
  detail: string;
  regimeLabel?: string;
  pnl?: number;
  positive?: boolean;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          {icon}
          <div>
            <p className="text-xs uppercase tracking-[0.18em] text-white/45">{title}</p>
            <p className="mt-1 text-lg font-semibold text-white">{headline}</p>
          </div>
        </div>
        {typeof pnl === "number" ? <Badge variant={positive || pnl >= 0 ? "positive" : "negative"}>{formatSignedCurrency(pnl)}</Badge> : null}
      </div>
      <p className="mt-4 text-sm leading-6 text-white/72">{detail}</p>
      {regimeLabel ? <p className="mt-3 text-xs text-white/45">Regime: {regimeLabel}</p> : null}
    </div>
  );
}

function BreakdownPanel({
  title,
  subtitle,
  rows,
  emptyLabel
}: {
  title: string;
  subtitle: string;
  rows: ReviewBreakdownRow[];
  emptyLabel: string;
}) {
  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-medium text-white/80">{title}</p>
        <p className="mt-1 text-xs text-white/50">{subtitle}</p>
      </div>
      <BreakdownTable rows={rows} emptyLabel={emptyLabel} />
    </div>
  );
}

function BreakdownTable({ rows, emptyLabel }: { rows: ReviewBreakdownRow[]; emptyLabel: string }) {
  if (rows.length === 0) {
    return <p className="text-sm leading-6 text-white/60">{emptyLabel}</p>;
  }

  return (
    <div className="overflow-x-auto rounded-2xl border border-white/10 bg-white/[0.03]">
      <Table className="min-w-[440px]">
        <TableHead>
          <TableRow>
            <TableHeaderCell>Bucket</TableHeaderCell>
            <TableHeaderCell>Trades</TableHeaderCell>
            <TableHeaderCell>Win Rate</TableHeaderCell>
            <TableHeaderCell>PnL</TableHeaderCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.label}>
              <TableCell className="font-medium text-white">{row.label}</TableCell>
              <TableCell>{row.trades}</TableCell>
              <TableCell>{formatPercent(row.winRate)}</TableCell>
              <TableCell className={row.totalPnl >= 0 ? "text-emerald-200" : "text-rose-200"}>{formatSignedCurrency(row.totalPnl)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function DragStat({
  label,
  value,
  negative = false
}: {
  label: string;
  value: string;
  negative?: boolean;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
      <p className="text-[11px] uppercase tracking-[0.16em] text-white/45">{label}</p>
      <p className={`mt-2 text-sm font-medium ${negative ? "text-rose-200" : "text-white"}`}>{value}</p>
    </div>
  );
}

function calculateReviewSlippageCost(rawEntry: number | null, filledEntry: number | null, quantity: number): number {
  if (rawEntry == null || filledEntry == null) {
    return 0;
  }

  return Math.max(filledEntry - rawEntry, 0) * quantity;
}

function calculateReviewExitSlippageCost(rawExit: number | null, filledExit: number | null, quantity: number): number {
  if (rawExit == null || filledExit == null) {
    return 0;
  }

  return Math.max(rawExit - filledExit, 0) * quantity;
}

function formatProfitFactor(value: number | null): string {
  if (value == null) {
    return "Not enough data";
  }
  if (!Number.isFinite(value)) {
    return "Clean so far";
  }
  return value.toFixed(2);
}

function formatWeekRange(startDate: string, endDate: string): string {
  const start = new Date(startDate);
  const end = new Date(new Date(endDate).getTime() - 1000);
  return `${start.toLocaleDateString("en-US", { month: "short", day: "numeric" })} - ${end.toLocaleDateString("en-US", { month: "short", day: "numeric" })}`;
}
