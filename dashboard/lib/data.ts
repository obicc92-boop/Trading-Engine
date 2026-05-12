import "server-only";

import { unstable_cache } from "next/cache";
import { cache } from "react";

import { buildPairedTradeComparisonSummary } from "@/lib/compare";
import { demoData } from "@/lib/demo-data";
import { buildOpenTradeMonitorSummary } from "@/lib/monitor";
import { buildPaperReviewSummary } from "@/lib/review";
import { createSupabaseAdminClient } from "@/lib/supabase/server";
import type {
  DashboardBundle,
  ExecutionLane,
  JournalEntryRecord,
  OpenTradeMonitorSummary,
  PairedTradeComparisonSummary,
  PaperReviewSummary,
  PerformanceLogRecord,
  SignalRecord,
  TradeRecord
} from "@/lib/types";


const DASHBOARD_REVALIDATE_SECONDS = 15;
type ExecutionModeFilter = ExecutionLane | "all";

async function queryWithFallback<T>(
  fallback: T,
  runner: (client: NonNullable<ReturnType<typeof createSupabaseAdminClient>>) => Promise<T | null>
): Promise<T> {
  const client = createSupabaseAdminClient();
  if (!client) {
    console.warn("[dashboard] Supabase client is not configured — NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing. Showing demo data.");
    return fallback;
  }

  try {
    const data = await runner(client);
    if (data === null) {
      console.warn("[dashboard] Supabase query returned no data. Showing demo data.");
    }
    return data ?? fallback;
  } catch (err) {
    console.error("[dashboard] Supabase query failed:", err);
    return fallback;
  }
}

const loadRecentSignals = unstable_cache(
  async (): Promise<SignalRecord[]> =>
    queryWithFallback(demoData.signals, async (client) => {
      const { data, error } = await client
        .from("signals")
        .select("*")
        .order("timestamp", { ascending: false })
        .limit(12);

      if (error) {
        console.error("[dashboard] signals query error:", error.message, error.code);
        return null;
      }

      return data as SignalRecord[];
    }),
  ["dashboard-recent-signals"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadRecentTrades = unstable_cache(
  async (mode: ExecutionModeFilter = "paper"): Promise<TradeRecord[]> =>
    queryWithFallback(
      mode === "all" ? demoData.trades : demoData.trades.filter((trade) => trade.execution_mode === mode),
      async (client) => {
        let query = client
          .from("trades")
          .select("*")
          .order("opened_at", { ascending: false })
          .limit(mode === "all" ? 120 : 24);

        if (mode !== "all") {
          query = query.eq("execution_mode", mode);
        }

        const { data, error } = await query;

        if (error) {
          console.error("[dashboard] trades query error:", error.message, error.code);
          return null;
        }

        return data as TradeRecord[];
      }
    ),
  ["dashboard-recent-trades"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadPendingApprovalTrades = unstable_cache(
  async (): Promise<TradeRecord[]> => {
    const client = createSupabaseAdminClient();
    if (!client) {
      return demoData.trades.filter((trade) => trade.status === "PENDING_APPROVAL");
    }

    try {
      const { data, error } = await client
        .from("trades")
        .select("*")
        .eq("status", "PENDING_APPROVAL")
        .order("opened_at", { ascending: false })
        .limit(12);

      if (error || !data) {
        return [];
      }

      return data as TradeRecord[];
    } catch {
      return [];
    }
  },
  ["dashboard-pending-approval-trades"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadRecentJournalEntries = unstable_cache(
  async (mode: ExecutionModeFilter = "paper"): Promise<JournalEntryRecord[]> =>
    queryWithFallback(filterJournalEntriesByMode(demoData.journalEntries, mode), async (client) => {
      const { data, error } = await client
        .from("journal_entries")
        .select("*")
        .order("timestamp", { ascending: false })
        .limit(40);

      if (error || !data) {
        return null;
      }

      return filterJournalEntriesByMode(data as JournalEntryRecord[], mode);
    }),
  ["dashboard-recent-journal-entries"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadRecentAICoachEntries = unstable_cache(
  async (mode: ExecutionModeFilter = "paper"): Promise<JournalEntryRecord[]> =>
    queryWithFallback(filterJournalEntriesByMode([], mode), async (client) => {
      const { data, error } = await client
        .from("journal_entries")
        .select("*")
        .contains("tags", ["ai-coach"])
        .order("timestamp", { ascending: false })
        .limit(30);

      if (error || !data) {
        return null;
      }

      return filterJournalEntriesByMode(data as JournalEntryRecord[], mode);
    }),
  ["dashboard-recent-ai-coach-entries"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadRecentReviewNotes = unstable_cache(
  async (mode: ExecutionModeFilter = "paper"): Promise<JournalEntryRecord[]> =>
    queryWithFallback(filterJournalEntriesByMode([], mode), async (client) => {
      const { data, error } = await client
        .from("journal_entries")
        .select("*")
        .contains("tags", ["review-note"])
        .order("timestamp", { ascending: false })
        .limit(40);

      if (error || !data) {
        return null;
      }

      return filterJournalEntriesByMode(data as JournalEntryRecord[], mode);
    }),
  ["dashboard-recent-review-notes"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadRecentPerformanceLogs = unstable_cache(
  async (mode: ExecutionModeFilter = "paper"): Promise<PerformanceLogRecord[]> =>
    queryWithFallback(
      mode === "all" ? demoData.performanceLogs : demoData.performanceLogs.filter((log) => log.execution_mode === mode),
      async (client) => {
        let query = client
          .from("performance_logs")
          .select("*")
          .order("timestamp", { ascending: false })
          .limit(50);

        if (mode !== "all") {
          query = query.eq("execution_mode", mode);
        }

        const { data, error } = await query;

        if (error || !data) {
          return null;
        }

        return data as PerformanceLogRecord[];
      }
    ),
  ["dashboard-recent-performance-logs"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadPaperReviewSignals = unstable_cache(
  async (): Promise<SignalRecord[]> =>
    queryWithFallback(demoData.signals, async (client) => {
      const { data, error } = await client
        .from("signals")
        .select("*")
        .order("timestamp", { ascending: false })
        .limit(200);

      if (error || !data) {
        return null;
      }

      return data as SignalRecord[];
    }),
  ["dashboard-paper-review-signals"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadPaperReviewTrades = unstable_cache(
  async (): Promise<TradeRecord[]> =>
    queryWithFallback(demoData.trades, async (client) => {
      const { data, error } = await client
        .from("trades")
        .select("*")
        .eq("execution_mode", "paper")
        .order("opened_at", { ascending: false })
        .limit(200);

      if (error || !data) {
        return null;
      }

      return data as TradeRecord[];
    }),
  ["dashboard-paper-review-trades"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadPaperReviewJournalEntries = unstable_cache(
  async (): Promise<JournalEntryRecord[]> =>
    queryWithFallback(demoData.journalEntries, async (client) => {
      const { data, error } = await client
        .from("journal_entries")
        .select("*")
        .order("timestamp", { ascending: false })
        .limit(200);

      if (error || !data) {
        return null;
      }

      return data as JournalEntryRecord[];
    }),
  ["dashboard-paper-review-journal-entries"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadPaperReviewPerformanceLogs = unstable_cache(
  async (): Promise<PerformanceLogRecord[]> =>
    queryWithFallback(demoData.performanceLogs, async (client) => {
      const { data, error } = await client
        .from("performance_logs")
        .select("*")
        .eq("execution_mode", "paper")
        .order("timestamp", { ascending: false })
        .limit(200);

      if (error || !data) {
        return null;
      }

      return data as PerformanceLogRecord[];
    }),
  ["dashboard-paper-review-performance-logs"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadPaperReviewSummary = unstable_cache(
  async (): Promise<PaperReviewSummary> => {
    const [signals, trades, journalEntries, performanceLogs] = await Promise.all([
      loadPaperReviewSignals(),
      loadPaperReviewTrades(),
      loadPaperReviewJournalEntries(),
      loadPaperReviewPerformanceLogs()
    ]);

    return buildPaperReviewSummary({
      signals,
      trades,
      journalEntries,
      performanceLogs
    });
  },
  ["dashboard-paper-review-summary"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadOpenTradeMonitor = unstable_cache(
  async (mode: ExecutionLane = "paper"): Promise<OpenTradeMonitorSummary> => {
    const [openTrades, signals, journalEntries] = await Promise.all([
      queryWithFallback(
        demoData.trades.filter((trade) => trade.execution_mode === mode && trade.status === "OPEN"),
        async (client) => {
          const { data, error } = await client
            .from("trades")
            .select("*")
            .eq("execution_mode", mode)
            .eq("status", "OPEN")
            .order("opened_at", { ascending: false })
            .limit(50);

          if (error || !data) {
            return null;
          }

          return data as TradeRecord[];
        }
      ),
      loadPaperReviewSignals(),
      loadPaperReviewJournalEntries()
    ]);

    return buildOpenTradeMonitorSummary({
      openTrades,
      signals,
      journalEntries,
      mode
    });
  },
  ["dashboard-open-trade-monitor"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

const loadPairedTradeComparisonSummary = unstable_cache(
  async (): Promise<PairedTradeComparisonSummary> => {
    const [trades, journalEntries] = await Promise.all([
      loadRecentTrades("all"),
      queryWithFallback(demoData.journalEntries, async (client) => {
        const { data, error } = await client
          .from("journal_entries")
          .select("*")
          .order("timestamp", { ascending: false })
          .limit(120);

        if (error || !data) {
          return null;
        }

        return data as JournalEntryRecord[];
      })
    ]);

    return buildPairedTradeComparisonSummary({
      trades,
      journalEntries
    });
  },
  ["dashboard-paired-trade-comparison"],
  { revalidate: DASHBOARD_REVALIDATE_SECONDS }
);

export const getRecentSignals = cache(loadRecentSignals);
export const getRecentTrades = cache(loadRecentTrades);
export const getPendingApprovalTrades = cache(loadPendingApprovalTrades);
export const getRecentJournalEntries = cache(loadRecentJournalEntries);
export const getRecentAICoachEntries = cache(loadRecentAICoachEntries);
export const getRecentReviewNotes = cache(loadRecentReviewNotes);
export const getRecentPerformanceLogs = cache(loadRecentPerformanceLogs);
export const getPaperReviewTrades = cache(loadPaperReviewTrades);
export const getPaperReviewSummary = cache(loadPaperReviewSummary);
export const getOpenTradeMonitor = cache(loadOpenTradeMonitor);
export const getPairedTradeComparisonSummary = cache(loadPairedTradeComparisonSummary);

export function getDashboardSettings(): DashboardBundle["settings"] {
  const paperLaneEnabled = process.env.PAPER_TRADING_ENABLED !== "false";
  const liveLaneEnabled = process.env.LIVE_TRADING_ENABLED === "true";
  const executionModeLabel =
    paperLaneEnabled && liveLaneEnabled
      ? "paper + live"
      : liveLaneEnabled
        ? "live"
        : process.env.EXECUTION_MODE ?? demoData.settings.executionMode;

  return {
    executionMode: executionModeLabel,
    bybitEnvironment: process.env.BYBIT_USE_TESTNET === "false" ? "mainnet" : demoData.settings.bybitEnvironment,
    riskPerTrade: process.env.RISK_PER_TRADE ? `${Number(process.env.RISK_PER_TRADE) * 100}%` : demoData.settings.riskPerTrade,
    dailyLossLimit: process.env.DAILY_LOSS_LIMIT ? `${Number(process.env.DAILY_LOSS_LIMIT) * 100}%` : demoData.settings.dailyLossLimit,
    confidenceThreshold: process.env.SIGNAL_CONFIDENCE_THRESHOLD ? `${process.env.SIGNAL_CONFIDENCE_THRESHOLD}%` : demoData.settings.confidenceThreshold,
    maxOpenTrades: process.env.MAX_OPEN_TRADES ?? demoData.settings.maxOpenTrades,
    orderPlacement: process.env.BYBIT_ALLOW_ORDER_PLACEMENT === "true" ? "Enabled" : demoData.settings.orderPlacement,
    manualConfirmation: process.env.BYBIT_REQUIRE_MANUAL_CONFIRMATION === "false" ? "Optional" : demoData.settings.manualConfirmation,
    liveRollout: [
      process.env.LIVE_TINY_SIZE_SCALE ? `${Math.round(Number(process.env.LIVE_TINY_SIZE_SCALE) * 100)}% size` : "10% size",
      process.env.LIVE_MAX_ORDER_NOTIONAL ? `max $${process.env.LIVE_MAX_ORDER_NOTIONAL}` : "max $25",
      process.env.LIVE_MAX_DAILY_ENTRIES ? `${process.env.LIVE_MAX_DAILY_ENTRIES} entry/day` : "1 entry/day"
    ].join(" | "),
    confirmationTtl: process.env.LIVE_CONFIRMATION_TTL_MINUTES ? `${process.env.LIVE_CONFIRMATION_TTL_MINUTES} minutes` : demoData.settings.confirmationTtl
  };
}

export async function getDashboardBundle(): Promise<DashboardBundle> {
  const [signals, trades, journalEntries, performanceLogs] = await Promise.all([
    getRecentSignals(),
    getRecentTrades(),
    getRecentJournalEntries(),
    getRecentPerformanceLogs()
  ]);

  return {
    signals,
    trades,
    journalEntries,
    performanceLogs,
    settings: getDashboardSettings()
  };
}

function filterJournalEntriesByMode(entries: JournalEntryRecord[], mode: ExecutionModeFilter): JournalEntryRecord[] {
  if (mode === "all") {
    return entries;
  }

  return entries.filter((entry) => {
    const tags = entry.tags ?? [];
    const laneTags = tags.filter((tag) => tag === "paper" || tag === "live");
    if (laneTags.length === 0) {
      return true;
    }
    return laneTags.includes(mode);
  });
}
