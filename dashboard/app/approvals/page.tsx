import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getRecentAICoachEntries } from "@/lib/data";
import { formatCompactDate } from "@/lib/utils";


export default async function ApprovalsPage() {
  const coachEntries = await getRecentAICoachEntries();

  return (
    <div className="space-y-6">
      <section className="rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6">
        <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Approvals</p>
        <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl">Automatic paper-entry mode is active.</h2>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
          Qualified paper BUY setups now open automatically again. You can use the dashboard for monitoring and the AI coach for explanation without stopping every trade for manual approval.
        </p>
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardDescription>Workflow</CardDescription>
            <CardTitle>How the simplified mode behaves</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 text-sm leading-6 text-white/70">
              <p>The engine scans your configured symbols and timeframes each cycle.</p>
              <p className="mt-3">When a BUY setup passes the strategy and risk filters in paper mode, the paper trade opens automatically.</p>
              <p className="mt-3">The dashboard then reflects the result in Signals, Performance, Trade Journal, and the open trade ledger.</p>
              <p className="mt-3">Use <span className="font-medium text-white">/coach</span> when you want the AI explanation without adding another approval checkpoint to every setup.</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>Coach context</CardDescription>
            <CardTitle>Latest AI coach notes</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {coachEntries.length > 0 ? (
              coachEntries.slice(0, 4).map((entry) => (
                <div key={`${entry.title}-${entry.timestamp}`} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h3 className="text-base font-semibold">{entry.title}</h3>
                    <Badge variant="neutral">{formatCompactDate(entry.timestamp)}</Badge>
                  </div>
                  <p className="mt-3 whitespace-pre-line text-sm leading-6 text-white/72">{entry.body}</p>
                </div>
              ))
            ) : (
              <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-6 text-sm leading-6 text-white/60">
                No AI coach notes have been saved yet. Send /coach in Telegram once the backend is running and the note will show up here.
              </div>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
