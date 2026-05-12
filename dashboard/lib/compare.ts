import type {
  JournalEntryRecord,
  PairedTradeComparisonRecord,
  PairedTradeComparisonSummary,
  TradeRecord
} from "@/lib/types";
import { getPaperSimulationSnapshot } from "@/lib/paper-simulation";


export function buildPairedTradeComparisonSummary({
  trades,
  journalEntries
}: {
  trades: TradeRecord[];
  journalEntries: JournalEntryRecord[];
}): PairedTradeComparisonSummary {
  const groupedTrades = new Map<string, TradeRecord[]>();

  for (const trade of trades) {
    if (trade.execution_mode !== "paper" && trade.execution_mode !== "live") {
      continue;
    }

    const tradeGroupId = extractTradeGroupId(trade.metadata);
    if (!tradeGroupId) {
      continue;
    }

    const existing = groupedTrades.get(tradeGroupId) ?? [];
    existing.push(trade);
    groupedTrades.set(tradeGroupId, existing);
  }

  const entries = [...groupedTrades.entries()]
    .map(([tradeGroupId, groupTrades]) =>
      buildComparisonRecord({
        tradeGroupId,
        groupTrades,
        journalEntries
      })
    )
    .sort((left, right) => toTime(right.updatedAt ?? right.openedAt) - toTime(left.updatedAt ?? left.openedAt));

  const paperOnlyTrades = entries.filter((entry) => entry.paperTrade && !entry.liveTrade).length;
  const liveOnlyTrades = entries.filter((entry) => !entry.paperTrade && entry.liveTrade).length;
  const pairedTrades = entries.filter((entry) => entry.paperTrade && entry.liveTrade).length;

  return {
    totalPairs: entries.length,
    pairedTrades,
    paperOnlyTrades,
    liveOnlyTrades,
    paperNetPnl: sum(entries.map((entry) => entry.paperPnl ?? 0)),
    liveNetPnl: sum(entries.map((entry) => entry.livePnl ?? 0)),
    netPnlDelta: sum(entries.map((entry) => (entry.livePnl ?? 0) - (entry.paperPnl ?? 0))),
    lastUpdated: entries.map((entry) => entry.updatedAt ?? entry.openedAt).find(Boolean) ?? null,
    entries
  };
}

function buildComparisonRecord({
  tradeGroupId,
  groupTrades,
  journalEntries
}: {
  tradeGroupId: string;
  groupTrades: TradeRecord[];
  journalEntries: JournalEntryRecord[];
}): PairedTradeComparisonRecord {
  const sortedTrades = [...groupTrades].sort((left, right) => toTime(right.closed_at ?? right.opened_at) - toTime(left.closed_at ?? left.opened_at));
  const paperTrade = sortedTrades.find((trade) => trade.execution_mode === "paper") ?? null;
  const liveTrade = sortedTrades.find((trade) => trade.execution_mode === "live") ?? null;
  const tradeIds = [paperTrade?.id, liveTrade?.id].filter(Boolean).map(String);
  const coachNotes = journalEntries
    .filter((entry) => {
      if (!entry.related_trade_id) {
        return false;
      }
      return tradeIds.includes(String(entry.related_trade_id));
    })
    .sort((left, right) => toTime(right.timestamp) - toTime(left.timestamp));

  return {
    tradeGroupId,
    symbol: paperTrade?.symbol ?? liveTrade?.symbol ?? "Unknown",
    interval: paperTrade?.interval ?? liveTrade?.interval ?? "Unknown",
    paperTrade,
    liveTrade,
    paperStatus: paperTrade?.status ?? null,
    liveStatus: liveTrade?.status ?? null,
    paperEntry: toNumberOrNull(paperTrade?.entry),
    liveEntry: toNumberOrNull(liveTrade?.entry),
    paperExit: toNumberOrNull(paperTrade?.exit_price),
    liveExit: toNumberOrNull(liveTrade?.exit_price),
    paperPnl: toNumberOrNull(paperTrade?.pnl),
    livePnl: toNumberOrNull(liveTrade?.pnl),
    paperFees: paperTrade && hasPaperSimulation(paperTrade) ? getPaperSimulationSnapshot(paperTrade).totalFeesPaid : null,
    liveFees: liveTrade && hasPaperSimulation(liveTrade) ? getPaperSimulationSnapshot(liveTrade).totalFeesPaid : null,
    entryDeltaPct: calculateDeltaPct(paperTrade?.entry, liveTrade?.entry),
    exitDeltaPct: calculateDeltaPct(paperTrade?.exit_price, liveTrade?.exit_price),
    openedAt: paperTrade?.opened_at ?? liveTrade?.opened_at ?? null,
    updatedAt: paperTrade?.closed_at ?? liveTrade?.closed_at ?? paperTrade?.opened_at ?? liveTrade?.opened_at ?? null,
    coachNotes
  };
}

function hasPaperSimulation(trade: TradeRecord): boolean {
  const metadata = trade.metadata;
  if (!metadata || typeof metadata !== "object") {
    return false;
  }

  return Boolean(metadata["paper_simulation"] && typeof metadata["paper_simulation"] === "object");
}

function extractTradeGroupId(metadata: Record<string, unknown> | undefined): string | null {
  if (!metadata) {
    return null;
  }

  const value = metadata["trade_group_id"];
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim();
  return normalized ? normalized : null;
}

function calculateDeltaPct(baseValue: number | null | undefined, comparisonValue: number | null | undefined): number | null {
  const base = Number(baseValue);
  const comparison = Number(comparisonValue);
  if (!Number.isFinite(base) || !Number.isFinite(comparison) || base === 0) {
    return null;
  }
  return ((comparison - base) / base) * 100;
}

function toNumberOrNull(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function toTime(value: unknown): number {
  if (!value) {
    return 0;
  }
  const parsed = new Date(String(value)).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
