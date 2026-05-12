import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getDashboardBundle } from "@/lib/data";
import { formatCompactDate } from "@/lib/utils";


const commands = [
  { command: "/signal", description: "Fetch the latest approved signals." },
  { command: "/live_pending", description: "List live trades waiting for manual confirmation." },
  { command: "/live_confirm <id>", description: "Confirm a pending live trade by id prefix." },
  { command: "/live_cancel <id>", description: "Cancel a pending live trade before any order is sent." },
  { command: "/performance", description: "Review current paper-trading performance." },
  { command: "/status", description: "Check execution mode, balances, and current posture." },
  { command: "/journal", description: "Read the most recent journal notes." },
  { command: "/coach", description: "Ask the Anthropic coach for a conservative next-step review." }
];

export default async function TelegramPage() {
  const bundle = await getDashboardBundle();
  const alertSignals = bundle.signals.filter((signal) => signal.eligible);

  return (
    <div className="space-y-6">
      <section className="rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6">
        <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Telegram</p>
        <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl">Real-time alerts without noise</h2>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
          Alerts are intentionally sparse. The goal is clarity and controlled action, not a constant stream of adrenaline.
        </p>
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardDescription>Bot commands</CardDescription>
            <CardTitle>Private operator toolkit</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {commands.map((item) => (
              <div key={item.command} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <code className="font-[var(--font-mono)] text-cyan-100">{item.command}</code>
                  <Badge variant="neutral">Enabled</Badge>
                </div>
                <p className="mt-3 text-sm text-white/65">{item.description}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>Recent alert-worthy signals</CardDescription>
            <CardTitle>Messages the bot may send</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {alertSignals.map((signal) => (
              <div key={`${signal.symbol}-${signal.timestamp}`} className="rounded-2xl border border-emerald-300/10 bg-emerald-300/5 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="text-lg font-semibold">{signal.symbol}</h3>
                    <p className="text-sm text-white/55">{formatCompactDate(signal.timestamp)}</p>
                  </div>
                  <Badge variant={signal.action === "BUY" ? "positive" : "negative"}>{signal.action}</Badge>
                </div>
                <p className="mt-4 whitespace-pre-line text-sm leading-6 text-white/70">{signal.reason}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardDescription>Anti-spam policy</CardDescription>
          <CardTitle>Why the bot stays quiet most of the time</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm leading-6 text-white/70">
          <p>Only high-confidence, eligible signals should trigger proactive alerts.</p>
          <p>Signal changes are deduplicated so the same market state does not keep interrupting you.</p>
          <p>Exit alerts are allowed even when confidence softens, because risk reduction deserves priority.</p>
          <p>Qualified paper BUY signals can open automatically again, while live entries stay human-confirmed through Telegram when enabled.</p>
          <p>The live rollout path clamps size aggressively by default before any Bybit order is submitted.</p>
        </CardContent>
      </Card>
    </div>
  );
}
