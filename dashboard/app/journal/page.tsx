import { ModeTabs } from "@/components/mode-tabs";
import { JournalFeed } from "@/components/journal-feed";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getRecentJournalEntries, getRecentReviewNotes } from "@/lib/data";


type PageProps = {
  searchParams?: Promise<{
    mode?: string;
  }>;
};

export default async function JournalPage({ searchParams }: PageProps) {
  const params = (await searchParams) ?? {};
  const activeMode = params.mode === "live" ? "live" : params.mode === "all" ? "all" : "paper";
  const [journalEntries, reviewNotes] = await Promise.all([getRecentJournalEntries(activeMode), getRecentReviewNotes(activeMode)]);

  return (
    <div className="space-y-6">
      <section className="rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Trade journal</p>
            <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl">Decision memory for better discipline</h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
              The journal keeps execution accountable. Switch between paper and live to review each lane cleanly, or use all to read the full story in one stream.
            </p>
          </div>
          <ModeTabs
            pathname="/journal"
            activeMode={activeMode}
            options={[
              { label: "Paper", value: "paper" },
              { label: "Live", value: "live" },
              { label: "All", value: "all" }
            ]}
          />
        </div>
      </section>

      <Card>
        <CardHeader>
          <CardDescription>Recent reflections</CardDescription>
          <CardTitle>{activeMode === "all" ? "Journal entries from every lane" : `${activeMode} lane journal entries`}</CardTitle>
        </CardHeader>
        <CardContent className="pt-2">
          {journalEntries.length > 0 ? (
            <JournalFeed entries={journalEntries} />
          ) : (
            <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-6 text-sm leading-6 text-white/60">
              No {activeMode === "all" ? "" : `${activeMode} `}journal entries yet. Once the backend writes reflections for this lane, they will show up here.
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardDescription>Replay archive</CardDescription>
          <CardTitle>{activeMode === "all" ? "Manual review notes with chart links" : `${activeMode} replay notes with chart links`}</CardTitle>
        </CardHeader>
        <CardContent className="pt-2">
          {reviewNotes.length > 0 ? (
            <JournalFeed entries={reviewNotes} />
          ) : (
            <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-6 text-sm leading-6 text-white/60">
              No manual review notes yet. Save one from Review Lab after a replay and it will show up here.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
