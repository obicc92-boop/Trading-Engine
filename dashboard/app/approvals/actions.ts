"use server";

import { revalidatePath } from "next/cache";

import { isUuid } from "@/lib/ids";
import { createSupabaseAdminClient } from "@/lib/supabase/server";


function revalidateApprovalViews() {
  for (const path of ["/", "/approvals", "/coach", "/journal", "/performance", "/signals"]) {
    revalidatePath(path);
  }
}

export async function approvePendingTradeAction(formData: FormData) {
  const client = createSupabaseAdminClient();
  if (!client) {
    throw new Error("Supabase admin client is unavailable.");
  }

  const tradeId = String(formData.get("trade_id") ?? "").trim();
  if (!tradeId) {
    throw new Error("Missing trade id.");
  }
  if (!isUuid(tradeId)) {
    revalidateApprovalViews();
    return;
  }

  const { data: trade, error: tradeError } = await client
    .from("trades")
    .select("*")
    .eq("id", tradeId)
    .eq("status", "PENDING_APPROVAL")
    .maybeSingle();

  if (tradeError) {
    throw new Error(tradeError.message);
  }
  if (!trade) {
    revalidateApprovalViews();
    return;
  }

  const timestamp = new Date().toISOString();
  const { error: updateError } = await client
    .from("trades")
    .update({
      status: "OPEN",
      opened_at: timestamp,
      notes: "Opened in paper trading mode after manual approval from the dashboard.",
    })
    .eq("id", tradeId);

  if (updateError) {
    throw new Error(updateError.message);
  }

  const { error: journalError } = await client.from("journal_entries").insert({
    title: "Paper trade opened",
    body: `Opened a paper trade for ${trade.symbol} on ${trade.interval} after manual approval from the dashboard.`,
    symbol: trade.symbol,
    related_trade_id: tradeId,
    tags: ["paper", "entry", "manual-approval", String(trade.interval).toLowerCase()],
    timestamp,
  });

  if (journalError) {
    throw new Error(journalError.message);
  }

  revalidateApprovalViews();
}

export async function rejectPendingTradeAction(formData: FormData) {
  const client = createSupabaseAdminClient();
  if (!client) {
    throw new Error("Supabase admin client is unavailable.");
  }

  const tradeId = String(formData.get("trade_id") ?? "").trim();
  if (!tradeId) {
    throw new Error("Missing trade id.");
  }
  if (!isUuid(tradeId)) {
    revalidateApprovalViews();
    return;
  }

  const { data: trade, error: tradeError } = await client
    .from("trades")
    .select("*")
    .eq("id", tradeId)
    .eq("status", "PENDING_APPROVAL")
    .maybeSingle();

  if (tradeError) {
    throw new Error(tradeError.message);
  }
  if (!trade) {
    revalidateApprovalViews();
    return;
  }

  const timestamp = new Date().toISOString();
  const { error: updateError } = await client
    .from("trades")
    .update({
      status: "CANCELLED",
      notes: "Rejected manually from the dashboard before opening the paper trade.",
    })
    .eq("id", tradeId);

  if (updateError) {
    throw new Error(updateError.message);
  }

  const { error: journalError } = await client.from("journal_entries").insert({
    title: "Paper trade rejected",
    body: `Rejected the pending paper trade for ${trade.symbol} on ${trade.interval} from the dashboard before entry.`,
    symbol: trade.symbol,
    related_trade_id: tradeId,
    tags: ["paper", "approval-rejected", String(trade.interval).toLowerCase()],
    timestamp,
  });

  if (journalError) {
    throw new Error(journalError.message);
  }

  revalidateApprovalViews();
}
