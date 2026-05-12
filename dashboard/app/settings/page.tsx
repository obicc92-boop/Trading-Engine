import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getDashboardSettings } from "@/lib/data";


export default function SettingsPage() {
  const settings = getDashboardSettings();

  return (
    <div className="space-y-6">
      <section className="rounded-[24px] border border-white/10 bg-white/[0.03] p-4 sm:rounded-[28px] sm:p-6">
        <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Settings</p>
        <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl">Safety posture before speed</h2>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">
          These settings define how cautious the platform stays. The defaults intentionally bias toward learning, paper execution, and manual oversight.
        </p>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader>
            <CardDescription>Execution</CardDescription>
            <CardTitle>{settings.executionMode}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-white/65">For live rollout, use `live` with order placement disabled first so the system can read balances without sending orders.</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Exchange</CardDescription>
            <CardTitle>{settings.bybitEnvironment}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-white/65">Bybit testnet is the default environment for safe rehearsal.</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Risk per trade</CardDescription>
            <CardTitle>{settings.riskPerTrade}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-white/65">The engine sizes positions from this cap before applying cash and notional constraints.</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Daily loss limit</CardDescription>
            <CardTitle>{settings.dailyLossLimit}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-white/65">New entries should be rejected once this loss threshold has been used.</CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardDescription>Operational flags</CardDescription>
          <CardTitle>Live-trading controls</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-white/45">Order placement</p>
            <div className="mt-3 flex flex-col items-start gap-3 sm:flex-row sm:items-center">
              <Badge variant={settings.orderPlacement === "Enabled" ? "negative" : "positive"}>{settings.orderPlacement}</Badge>
              <span className="text-sm text-white/65">Withdrawals are intentionally excluded from platform scope.</span>
            </div>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-white/45">Manual confirmation</p>
            <div className="mt-3 flex flex-col items-start gap-3 sm:flex-row sm:items-center">
              <Badge variant="accent">{settings.manualConfirmation}</Badge>
              <span className="text-sm text-white/65">Real-money entries can stay Telegram-confirmed even after paper trading is automated.</span>
            </div>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-white/45">Confidence threshold</p>
            <p className="mt-3 text-lg font-semibold">{settings.confidenceThreshold}</p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-white/45">Open trade cap</p>
            <p className="mt-3 text-lg font-semibold">{settings.maxOpenTrades}</p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-white/45">Live rollout</p>
            <p className="mt-3 text-lg font-semibold">{settings.liveRollout}</p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-white/45">Confirmation TTL</p>
            <p className="mt-3 text-lg font-semibold">{settings.confirmationTtl}</p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
