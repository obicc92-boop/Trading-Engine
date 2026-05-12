import { SignalsTable } from "@/components/signals-table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getRecentSignals } from "@/lib/data";


export default async function SignalsPage() {
  const signals = await getRecentSignals();
  const approvedSignals = signals.filter((signal) => signal.eligible).length;

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-2xl">
          <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Signals</p>
          <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl">Only the setups worth attention</h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
            This page keeps weak setups visible for learning while separating the approved ones from the noise.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Badge variant="accent">{approvedSignals} approved</Badge>
          <Badge variant="neutral">{signals.length} logged</Badge>
        </div>
      </section>

      <Card>
        <CardHeader>
          <CardDescription>Supabase signal stream</CardDescription>
          <CardTitle>Recent reads from the Python engine</CardTitle>
        </CardHeader>
        <CardContent className="pt-2">
          <SignalsTable signals={signals} />
        </CardContent>
      </Card>
    </div>
  );
}
