"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";

import {
  initialReviewNoteState,
  saveReviewNoteAction,
  type ReviewNoteActionState
} from "@/app/review/actions";
import { Button } from "@/components/ui/button";
import type { TradeRecord } from "@/lib/types";
import { formatCompactDate } from "@/lib/utils";


export function ReviewNoteForm({ trades }: { trades: TradeRecord[] }) {
  const [state, formAction] = useActionState<ReviewNoteActionState, FormData>(
    saveReviewNoteAction,
    initialReviewNoteState
  );

  if (trades.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-5 text-sm leading-6 text-white/60">
        There are no recent paper trades available for review notes yet. Let the engine close a few paper positions first, then this form will become useful.
      </div>
    );
  }

  return (
    <form action={formAction} className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <label className="space-y-2 text-sm text-white/72">
          <span className="block text-xs uppercase tracking-[0.18em] text-white/45">Trade</span>
          <select
            name="trade_id"
            required
            defaultValue={String(trades[0]?.id ?? "")}
            className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-300/40"
          >
            {trades.map((trade) => (
              <option key={String(trade.id ?? `${trade.symbol}-${trade.opened_at}`)} value={String(trade.id ?? "")}>
                {trade.symbol} {trade.interval} | {trade.status} | {formatCompactDate(trade.closed_at ?? trade.opened_at)}
              </option>
            ))}
          </select>
        </label>

        <label className="space-y-2 text-sm text-white/72">
          <span className="block text-xs uppercase tracking-[0.18em] text-white/45">Title</span>
          <input
            name="title"
            type="text"
            placeholder="Optional custom title"
            className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-cyan-300/40"
          />
        </label>
      </div>

      <label className="space-y-2 text-sm text-white/72">
        <span className="block text-xs uppercase tracking-[0.18em] text-white/45">Reflection</span>
        <textarea
          name="reflection"
          required
          rows={5}
          placeholder="What happened in this trade, what you felt, and what you would repeat or change next time."
          className="w-full rounded-[24px] border border-white/10 bg-slate-950/80 px-4 py-3 text-sm leading-6 text-white outline-none transition placeholder:text-white/30 focus:border-cyan-300/40"
        />
      </label>

      <div className="grid gap-4 lg:grid-cols-2">
        <label className="space-y-2 text-sm text-white/72">
          <span className="block text-xs uppercase tracking-[0.18em] text-white/45">Lesson</span>
          <input
            name="lesson"
            type="text"
            placeholder="The one sentence you want future-you to remember"
            className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-cyan-300/40"
          />
        </label>
        <label className="space-y-2 text-sm text-white/72">
          <span className="block text-xs uppercase tracking-[0.18em] text-white/45">Mistake tags</span>
          <input
            name="mistake_tags"
            type="text"
            placeholder="early entry, moved stop, FOMO"
            className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-cyan-300/40"
          />
        </label>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <label className="space-y-2 text-sm text-white/72">
          <span className="block text-xs uppercase tracking-[0.18em] text-white/45">Before chart URL</span>
          <input
            name="before_chart_url"
            type="url"
            placeholder="https://..."
            className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-cyan-300/40"
          />
        </label>
        <label className="space-y-2 text-sm text-white/72">
          <span className="block text-xs uppercase tracking-[0.18em] text-white/45">After chart URL</span>
          <input
            name="after_chart_url"
            type="url"
            placeholder="https://..."
            className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-cyan-300/40"
          />
        </label>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p
          className={`text-sm ${
            state.status === "error" ? "text-rose-200" : state.status === "success" ? "text-emerald-200" : "text-white/50"
          }`}
        >
          {state.message || "Saved notes will appear in Review Lab and the journal feed."}
        </p>
        <SubmitButton />
      </div>
    </form>
  );
}

function SubmitButton() {
  const { pending } = useFormStatus();

  return (
    <Button type="submit" disabled={pending}>
      {pending ? "Saving note..." : "Save review note"}
    </Button>
  );
}
