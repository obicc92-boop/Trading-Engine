import { BrainCircuit, ShieldCheck } from "lucide-react";

import { JournalFeed } from "@/components/journal-feed";
import { ModeTabs } from "@/components/mode-tabs";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getRecentAICoachEntries } from "@/lib/data";


type PageProps = {
  searchParams?: Promise<{
    mode?: string;
  }>;
};

export default async function CoachPage({ searchParams }: PageProps) {
  const params = (await searchParams) ?? {};
  const activeMode = params.mode === "live" ? "live" : params.mode === "all" ? "all" : "paper";
  const coachEntries = await getRecentAICoachEntries(activeMode);
  const coachEnabled = process.env.AI_COACH_ENABLED !== "false";
  const coachModel = process.env.ANTHROPIC_MODEL ?? "claude-sonnet-4-20250514";
  const hasApiKey = Boolean(process.env.ANTHROPIC_API_KEY);

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-2xl">
          <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">AI Coach</p>
          <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl">A conservative second set of eyes for your process.</h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
            The coach is read-only in this build. It should help you slow down, respect risk, and journal better, not bypass your rule set.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <ModeTabs
            pathname="/coach"
            activeMode={activeMode}
            options={[
              { label: "Paper", value: "paper" },
              { label: "Live", value: "live" },
              { label: "All", value: "all" }
            ]}
          />
          <Badge variant={coachEnabled && hasApiKey ? "positive" : "negative"}>
            {coachEnabled && hasApiKey ? "Coach ready" : "Coach unavailable"}
          </Badge>
          <Badge variant="neutral">{coachModel}</Badge>
          <Badge variant="accent">Telegram /coach</Badge>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardDescription>How to use it</CardDescription>
            <CardTitle>Current workflow</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm leading-6 text-white/72">
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <div className="flex items-center gap-3">
                <BrainCircuit className="h-4 w-4 text-cyan-200" />
                <p>Ask for guidance in Telegram with <code className="font-[var(--font-mono)] text-cyan-100">/coach</code>.</p>
              </div>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <div className="flex items-center gap-3">
                <ShieldCheck className="h-4 w-4 text-emerald-200" />
                <p>The coach stays inside paper-mode, spot-only, long-only rules and should default to caution when the setup is unclear.</p>
              </div>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-white/45">Outputs you should expect</p>
              <p className="mt-3">Verdict, summary, reasons, risks, checklist, and a journal prompt saved into your review trail.</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>Availability</CardDescription>
            <CardTitle>Coach status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm leading-6 text-white/72">
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-white/45">Enabled</p>
              <p className="mt-2 text-lg font-semibold">{coachEnabled ? "Yes" : "No"}</p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-white/45">Anthropic key detected</p>
              <p className="mt-2 text-lg font-semibold">{hasApiKey ? "Yes" : "No"}</p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-white/45">Model</p>
              <p className="mt-2 text-lg font-semibold">{coachModel}</p>
            </div>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardDescription>Latest coach notes</CardDescription>
          <CardTitle>{activeMode === "all" ? "Saved AI guidance across all lanes" : `Saved ${activeMode} AI guidance`}</CardTitle>
        </CardHeader>
        <CardContent className="pt-2">
          {coachEntries.length > 0 ? (
            <JournalFeed entries={coachEntries} />
          ) : (
            <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-6 text-sm leading-6 text-white/60">
              No AI coach notes have been saved yet. Start the backend, send <code className="font-[var(--font-mono)] text-cyan-100">/coach</code> in Telegram,
              and the recommendation will show up here after it is written to the journal stream.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
