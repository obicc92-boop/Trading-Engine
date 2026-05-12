"use server";

import { revalidatePath } from "next/cache";

import { isUuid } from "@/lib/ids";
import { createSupabaseAdminClient } from "@/lib/supabase/server";


export type ReviewNoteActionState = {
  status: "idle" | "success" | "error";
  message: string;
};

export const initialReviewNoteState: ReviewNoteActionState = {
  status: "idle",
  message: ""
};

function revalidateReviewViews() {
  for (const path of ["/review", "/journal", "/performance"]) {
    revalidatePath(path);
  }
}

export async function saveReviewNoteAction(
  _previousState: ReviewNoteActionState,
  formData: FormData
): Promise<ReviewNoteActionState> {
  const client = createSupabaseAdminClient();
  if (!client) {
    return {
      status: "error",
      message: "Supabase admin client is unavailable."
    };
  }

  const tradeId = String(formData.get("trade_id") ?? "").trim();
  const title = String(formData.get("title") ?? "").trim();
  const reflection = String(formData.get("reflection") ?? "").trim();
  const lesson = String(formData.get("lesson") ?? "").trim();
  const mistakeTags = String(formData.get("mistake_tags") ?? "").trim();
  const beforeChartUrl = normalizeUrl(String(formData.get("before_chart_url") ?? "").trim());
  const afterChartUrl = normalizeUrl(String(formData.get("after_chart_url") ?? "").trim());

  if (!tradeId || !isUuid(tradeId)) {
    return {
      status: "error",
      message: "Choose a real trade before saving the note."
    };
  }
  if (!reflection) {
    return {
      status: "error",
      message: "Add a reflection so the note teaches you something later."
    };
  }

  const { data: trade, error: tradeError } = await client
    .from("trades")
    .select("*")
    .eq("id", tradeId)
    .eq("execution_mode", "paper")
    .maybeSingle();

  if (tradeError) {
    return {
      status: "error",
      message: tradeError.message
    };
  }
  if (!trade) {
    return {
      status: "error",
      message: "That trade was not found in the paper-trading history."
    };
  }

  const regimeSlug = extractRegimeSlug(trade.metadata);
  const normalizedMistakeTags = mistakeTags
    .split(",")
    .map((item) => toTag(item))
    .filter(Boolean)
    .slice(0, 6);

  const bodySections = [
    `Reflection:\n${reflection}`,
    lesson ? `Lesson:\n${lesson}` : "",
    beforeChartUrl ? `Before chart:\n${beforeChartUrl}` : "",
    afterChartUrl ? `After chart:\n${afterChartUrl}` : ""
  ].filter(Boolean);

  const timestamp = new Date().toISOString();
  const reviewTitle = title || `Review note: ${trade.symbol} ${trade.interval}`;
  const tags = [
    "review-note",
    "manual-review",
    String(trade.interval ?? "").toLowerCase(),
    regimeSlug,
    ...normalizedMistakeTags
  ].filter(Boolean);

  const { error: insertError } = await client.from("journal_entries").insert({
    title: reviewTitle,
    body: bodySections.join("\n\n"),
    symbol: trade.symbol,
    related_trade_id: tradeId,
    tags,
    timestamp
  });

  if (insertError) {
    return {
      status: "error",
      message: insertError.message
    };
  }

  revalidateReviewViews();
  return {
    status: "success",
    message: "Review note saved to the journal."
  };
}

function normalizeUrl(value: string): string {
  if (!value) {
    return "";
  }
  if (value.startsWith("http://") || value.startsWith("https://")) {
    return value;
  }
  return "";
}

function toTag(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function extractRegimeSlug(metadata: unknown): string {
  if (!metadata || typeof metadata !== "object") {
    return "";
  }

  const regime = (metadata as Record<string, unknown>).regime;
  if (!regime || typeof regime !== "object") {
    return "";
  }

  return String((regime as Record<string, unknown>).slug ?? "").trim().toLowerCase();
}
