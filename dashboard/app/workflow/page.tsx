import type { ReactNode } from "react";
import { ArrowRight, BrainCircuit, Compass, ShieldCheck, TimerReset } from "lucide-react";

import { MetricCard } from "@/components/metric-card";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getDashboardSettings,
  getPaperReviewSummary,
  getRecentAICoachEntries,
  getRecentPerformanceLogs,
  getRecentSignals
} from "@/lib/data";
import { formatCompactDate, formatCurrency, formatPercent, formatSignedCurrency } from "@/lib/utils";


const preSessionChecklist = [
  "Start the backend and confirm the dashboard is reading fresh paper data.",
  "Open Review Lab and check the biggest recent mistake before doing anything else.",
  "Read the current streak, drawdown, and dominant weak regime so you know what you are trying not to repeat.",
  "Choose one behavior to improve today. Keep it behavioral, not PnL-based."
];

const duringSessionChecklist = [
  "Watch for qualified signals, but do not judge the system on a single trade.",
  "Use /coach when a setup feels tempting or confusing.",
  "Let the engine stay systematic. Your job is observation, not interference.",
  "Notice when you feel urgency, boredom, revenge, or overconfidence. Those are study moments."
];

const afterSessionChecklist = [
  "Open Review Lab and inspect expectancy, drawdown, weakest symbol, weakest interval, and weakest regime.",
  "Replay the worst trade first, not the best one.",
  "Save one review note with the lesson and chart links, even if the day felt boring.",
  "Only consider changing rules after patterns repeat over enough trades."
];

const weeklyChecklist = [
  "Compare this week versus last week in Review Lab.",
  "Look for repeated failure clusters instead of random bad luck.",
  "Change one thing at a time, then let paper mode collect a fresh sample.",
  "Stay out of live trading until the edge is stable, understandable, and emotionally boring."
];

const telegramCommands = ["/status", "/signal", "/coach", "/performance", "/journal"];

export default async function WorkflowPage() {
  const [summary, settings, signals, coachEntries, performanceLogs] = await Promise.all([
    getPaperReviewSummary(),
    Promise.resolve(getDashboardSettings()),
    getRecentSignals(),
    getRecentAICoachEntries(),
    getRecentPerformanceLogs()
  ]);

  const latestPerformance = performanceLogs[0];
  const approvedSignals = signals.filter((signal) => signal.eligible);
  const topSignal = approvedSignals[0];
  const weakestRegime = [...summary.regimeBreakdown].sort((left, right) => left.totalPnl - right.totalPnl)[0];
  const weakestSymbol = [...summary.symbolBreakdown].sort((left, right) => left.totalPnl - right.totalPnl)[0];
  const weeklyDelta = summary.weeklyComparison.current.totalPnl - summary.weeklyComparison.previous.totalPnl;
  const expectancyDelta = summary.weeklyComparison.current.expectancy - summary.weeklyComparison.previous.expectancy;
  const latestCoachNote = coachEntries[0];

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Daily workflow</p>
          <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl lg:text-4xl">Run the app like a training desk, not a dopamine machine.</h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
            This page turns your routine into a repeatable loop: prepare, observe, review, and only then adjust. When the process is strong enough, the performance numbers become easier to trust.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Badge variant="accent">{settings.executionMode.toUpperCase()} MODE</Badge>
          <Badge variant="neutral">Risk {settings.riskPerTrade}</Badge>
          <Badge variant="positive">Loss limit {settings.dailyLossLimit}</Badge>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          title="Today's Posture"
          value={summary.currentStreakLabel}
          description="Start the day knowing whether you are coming off confidence or pain."
          footnote={<Badge variant="neutral">{summary.openTrades} open paper trades</Badge>}
        />
        <MetricCard
          title="Weakest Regime"
          value={weakestRegime?.label ?? "No data yet"}
          description="The market context that is hurting the paper results most right now."
          footnote={<Badge variant={weakestRegime && weakestRegime.totalPnl >= 0 ? "positive" : "negative"}>{formatSignedCurrency(weakestRegime?.totalPnl ?? 0)}</Badge>}
        />
        <MetricCard
          title="Week-on-Week PnL"
          value={formatSignedCurrency(weeklyDelta)}
          description="Current week total PnL minus last week total PnL."
          footnote={<Badge variant={weeklyDelta >= 0 ? "positive" : "negative"}>Expectancy delta {formatSignedCurrency(expectancyDelta)}</Badge>}
        />
        <MetricCard
          title="Approved Signals"
          value={String(approvedSignals.length)}
          description="How many recent signals currently cleared the filters."
          footnote={<Badge variant={approvedSignals.length > 0 ? "positive" : "neutral"}>{topSignal ? `${topSignal.symbol} ${topSignal.interval}` : "No active setup"}</Badge>}
        />
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <Card>
          <CardHeader>
            <CardDescription>Current focus</CardDescription>
            <CardTitle>What deserves attention before you start</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 pt-2">
            <FocusCard
              icon={<Compass className="h-4 w-4 text-cyan-200" />}
              title="Primary weakness"
              body={
                weakestSymbol
                  ? `${weakestSymbol.label} is the biggest symbol leak right now at ${formatSignedCurrency(weakestSymbol.totalPnl)}. If it shows up today, watch it with extra skepticism.`
                  : "Not enough closed paper trades yet to identify a weak symbol cluster."
              }
            />
            <FocusCard
              icon={<ShieldCheck className="h-4 w-4 text-emerald-200" />}
              title="Review prompt"
              body={summary.reviewPrompts[0] ?? "Keep following the paper process and wait for enough data to form a real pattern."}
            />
            <FocusCard
              icon={<BrainCircuit className="h-4 w-4 text-cyan-200" />}
              title="Coach context"
              body={
                latestCoachNote
                  ? `Latest coach note saved ${formatCompactDate(latestCoachNote.timestamp)}. Re-read it before the session if the same setup type is showing up again.`
                  : "No recent coach note saved yet. Use /coach when a setup feels emotionally loud or unclear."
              }
            />
            <FocusCard
              icon={<TimerReset className="h-4 w-4 text-amber-200" />}
              title="Risk posture"
              body={`Current paper daily PnL is ${formatSignedCurrency(latestPerformance?.realized_pnl_today ?? 0)} with total realized PnL at ${formatSignedCurrency(summary.totalRealizedPnl)}. Respect the loss limit even when the app is only papering.`}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>Fast operator panel</CardDescription>
            <CardTitle>What to open and what to run</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 pt-2">
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-white/45">Pages to keep nearby</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Badge variant="accent">/workflow</Badge>
                <Badge variant="neutral">/positions</Badge>
                <Badge variant="neutral">/review</Badge>
                <Badge variant="neutral">/signals</Badge>
                <Badge variant="neutral">/coach</Badge>
              </div>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-white/45">Telegram toolkit</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {telegramCommands.map((command) => (
                  <Badge key={command} variant="neutral">
                    {command}
                  </Badge>
                ))}
              </div>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-white/45">Top active setup</p>
              {topSignal ? (
                <div className="mt-3 space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-lg font-semibold text-white">{topSignal.symbol}</p>
                      <p className="text-sm text-white/55">{topSignal.interval}</p>
                    </div>
                    <Badge variant={topSignal.action === "BUY" ? "positive" : topSignal.action === "SELL" ? "negative" : "neutral"}>
                      {topSignal.action}
                    </Badge>
                  </div>
                  <p className="text-sm text-white/70">Confidence {topSignal.confidence}%</p>
                  <p className="text-sm leading-6 text-white/65">{topSignal.reason?.split("\n")[0]?.replace(/^- /, "") ?? "No reason logged yet."}</p>
                </div>
              ) : (
                <p className="mt-3 text-sm leading-6 text-white/60">No active approved setup right now. That is fine. Quiet tape is part of the workflow.</p>
              )}
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-6 xl:grid-cols-2">
        <WorkflowCard
          stepLabel="Pre-session"
          title="Prepare before the market starts talking"
          description="Start by checking context, not by hunting trades."
          items={preSessionChecklist}
        />
        <WorkflowCard
          stepLabel="During session"
          title="Observe without forcing action"
          description="The goal during the session is clean observation and disciplined reaction."
          items={duringSessionChecklist}
        />
        <WorkflowCard
          stepLabel="After session"
          title="Turn outcomes into lessons"
          description="This is where the app becomes a teacher instead of just a monitor."
          items={afterSessionChecklist}
        />
        <WorkflowCard
          stepLabel="Weekly rhythm"
          title="Zoom out before you tweak anything"
          description="Your adjustments should come from repeated evidence, not one emotional day."
          items={weeklyChecklist}
        />
      </section>

      <Card>
        <CardHeader>
          <CardDescription>Success criteria</CardDescription>
          <CardTitle>How to know the routine is working</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 pt-2 lg:grid-cols-3">
          <SuccessBlock
            title="Behavior first"
            body="You are using the same routine every day, saving review notes, and your mistakes are becoming easier to name."
          />
          <SuccessBlock
            title="Stats second"
            body={`Expectancy is ${formatSignedCurrency(summary.expectancy)}, drawdown is ${formatPercent(summary.maxDrawdownPct)}, and those numbers are getting more understandable, not just more exciting.`}
          />
          <SuccessBlock
            title="Patience last"
            body="You feel less urge to rush live trading because the paper process is becoming stable, teachable, and a little boring."
          />
        </CardContent>
      </Card>
    </div>
  );
}

function WorkflowCard({
  stepLabel,
  title,
  description,
  items
}: {
  stepLabel: string;
  title: string;
  description: string;
  items: string[];
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{stepLabel}</CardDescription>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 pt-2">
        <p className="text-sm leading-6 text-white/65">{description}</p>
        {items.map((item) => (
          <div key={item} className="flex gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <ArrowRight className="mt-0.5 h-4 w-4 shrink-0 text-cyan-200" />
            <p className="text-sm leading-6 text-white/72">{item}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function FocusCard({
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

function SuccessBlock({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
      <p className="text-xs uppercase tracking-[0.18em] text-white/45">{title}</p>
      <p className="mt-3 text-sm leading-6 text-white/72">{body}</p>
    </div>
  );
}
