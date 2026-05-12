import type {
  ExecutionLane,
  JournalEntryRecord,
  OpenTradeMonitorRecord,
  OpenTradeMonitorSummary,
  SignalIndicators,
  SignalRecord,
  TradeRecord
} from "@/lib/types";
import { getPaperSimulationSnapshot } from "@/lib/paper-simulation";


const DEFAULT_REGIME_LABEL = "Unclassified";

export function buildOpenTradeMonitorSummary({
  openTrades,
  signals,
  journalEntries,
  mode
}: {
  openTrades: TradeRecord[];
  signals: SignalRecord[];
  journalEntries: JournalEntryRecord[];
  mode: ExecutionLane;
}): OpenTradeMonitorSummary {
  const laneOpenTrades = openTrades
    .filter((trade) => trade.execution_mode === mode && trade.status === "OPEN")
    .sort((left, right) => toTime(right.opened_at) - toTime(left.opened_at));
  const sortedSignals = [...signals].sort((left, right) => toTime(right.timestamp) - toTime(left.timestamp));
  const sortedJournalEntries = [...journalEntries].sort((left, right) => toTime(right.timestamp) - toTime(left.timestamp));

  const entries = laneOpenTrades.map((trade) =>
    buildOpenTradeMonitorRecord({
      trade,
      signals: sortedSignals,
      journalEntries: sortedJournalEntries
    })
  );

  const nearestStopTrade = [...entries]
    .filter((entry) => entry.distanceToStopPct != null)
    .sort((left, right) => (left.distanceToStopPct ?? Number.POSITIVE_INFINITY) - (right.distanceToStopPct ?? Number.POSITIVE_INFINITY))[0];
  const highestConvictionTrade = [...entries].sort((left, right) => (right.lastSignalConfidence ?? right.trade.confidence ?? 0) - (left.lastSignalConfidence ?? left.trade.confidence ?? 0))[0];
  const latestTimestamps = entries
    .map((entry) => entry.lastSignalTimestamp)
    .filter((value): value is string => Boolean(value))
    .sort((left, right) => toTime(right) - toTime(left));

  return {
    totalOpenTrades: entries.length,
    totalUnrealizedPnl: sum(entries.map((entry) => entry.unrealizedPnl)),
    averageUnrealizedPct: average(entries.map((entry) => entry.unrealizedPct)),
    nearestStopLabel: nearestStopTrade ? `${nearestStopTrade.trade.symbol} ${nearestStopTrade.trade.interval}` : "No open trades",
    highestConvictionLabel: highestConvictionTrade ? `${highestConvictionTrade.trade.symbol} ${highestConvictionTrade.trade.interval}` : "No open trades",
    lastUpdated: latestTimestamps[0] ?? null,
    entries
  };
}

function buildOpenTradeMonitorRecord({
  trade,
  signals,
  journalEntries
}: {
  trade: TradeRecord;
  signals: SignalRecord[];
  journalEntries: JournalEntryRecord[];
}): OpenTradeMonitorRecord {
  const latestSignal = findLatestSignalForTrade(trade, signals);
  const currentPrice = resolveCurrentPrice(trade, latestSignal);
  const entryPrice = toNumber(trade.entry);
  const positionSize = toNumber(trade.position_size);
  const takeProfit = toNumber(trade.take_profit);
  const stopLoss = toNumber(trade.stop_loss);
  const simulation = getPaperSimulationSnapshot(trade);
  const feeRate = simulation.feeRate;
  const entryFeePaid = simulation.entryFeePaid;
  const effectiveEntryFeePaid = simulation.remainingEntryFeePaid || simulation.entryFeePaid;
  const estimatedExitFee = currentPrice * positionSize * feeRate;
  const unrealizedPnl =
    simulation.partialRealizedPnl + (currentPrice - entryPrice) * positionSize - effectiveEntryFeePaid - estimatedExitFee;
  const pnlBase = simulation.totalEntryCost ?? (entryPrice > 0 && positionSize > 0 ? entryPrice * positionSize : 0);
  const unrealizedPct = pnlBase > 0 ? (unrealizedPnl / pnlBase) * 100 : 0;
  const distanceToStopPct = currentPrice > 0 && stopLoss > 0 ? ((currentPrice - stopLoss) / currentPrice) * 100 : null;
  const distanceToTargetPct = currentPrice > 0 && takeProfit > 0 ? ((takeProfit - currentPrice) / currentPrice) * 100 : null;
  const targetProgressPct = takeProfit > entryPrice ? ((currentPrice - entryPrice) / (takeProfit - entryPrice)) * 100 : null;
  const relatedJournalEntries = journalEntries.filter((entry) => String(entry.related_trade_id ?? "") === String(trade.id ?? ""));
  const coachNote = relatedJournalEntries.find((entry) => (entry.tags ?? []).includes("ai-coach")) ?? null;

  return {
    trade,
    currentPrice,
    unrealizedPnl,
    unrealizedPct,
    entryPriceRaw: simulation.entryPriceRaw,
    entryFeePaid,
    estimatedExitFee,
    totalFeesIfClosedNow: entryFeePaid + simulation.partialExitFeePaid + estimatedExitFee,
    simulationSlippageBps: simulation.slippageBps,
    sizeCappedForCash: simulation.sizeCappedForCash,
    distanceToStopPct,
    distanceToTargetPct,
    targetProgressPct,
    durationHours: calculateDurationHours(trade.opened_at),
    regimeLabel: resolveRegimeLabel(trade, latestSignal),
    lastSignalTimestamp: latestSignal?.timestamp ?? null,
    lastSignalAction: latestSignal?.action ?? null,
    lastSignalConfidence: latestSignal?.confidence ?? trade.confidence ?? null,
    lastSignalReason: summarizeSignalReason(latestSignal?.reason),
    coachNote
  };
}

function findLatestSignalForTrade(trade: TradeRecord, signals: SignalRecord[]): SignalRecord | null {
  return (
    signals.find((signal) => signal.symbol === trade.symbol && signal.interval === trade.interval) ??
    signals.find((signal) => signal.symbol === trade.symbol) ??
    null
  );
}

function resolveCurrentPrice(trade: TradeRecord, latestSignal: SignalRecord | null): number {
  const signalClose = latestSignal?.indicators?.close;
  if (typeof signalClose === "number" && Number.isFinite(signalClose)) {
    return signalClose;
  }

  const signalEntry = latestSignal?.entry;
  if (typeof signalEntry === "number" && Number.isFinite(signalEntry)) {
    return signalEntry;
  }

  return toNumber(trade.entry);
}

function resolveRegimeLabel(trade: TradeRecord, latestSignal: SignalRecord | null): string {
  const metadataRegime = extractMetadataRegimeLabel(trade.metadata);
  if (metadataRegime) {
    return metadataRegime;
  }

  if (latestSignal?.indicators) {
    return inferRegimeLabel(latestSignal.indicators);
  }

  return DEFAULT_REGIME_LABEL;
}

function extractMetadataRegimeLabel(metadata: Record<string, unknown> | undefined): string | null {
  if (!metadata) {
    return null;
  }

  const regime = metadata["regime"];
  if (!regime || typeof regime !== "object") {
    return null;
  }

  const label = (regime as Record<string, unknown>).label;
  if (typeof label === "string" && label.trim()) {
    return label.trim();
  }

  return null;
}

function inferRegimeLabel(indicators: SignalIndicators): string {
  const closePrice = toNumber(indicators.close);
  const ma50 = toNumber(indicators.ma_50);
  const ma200 = toNumber(indicators.ma_200);
  const rsi = toNumber(indicators.rsi_14);
  const volatilityPct = ma50 > 0 ? (Math.abs(closePrice - ma50) / ma50) * 100 : 0;
  const highVolatility = volatilityPct >= 2.5 || Math.abs(rsi - 50) >= 18;

  if (closePrice > ma50 && ma50 >= ma200 && rsi >= 55) {
    return highVolatility ? "Trend / high volatility" : "Trend / steady volatility";
  }
  if (closePrice < ma50 && ma50 <= ma200 && rsi <= 45) {
    return highVolatility ? "Selloff / high volatility" : "Bearish pressure";
  }
  if (Math.abs(rsi - 50) <= 6) {
    return "Range / indecision";
  }
  return "Transition / mixed tape";
}

function summarizeSignalReason(reason: string | undefined): string {
  if (!reason) {
    return "No fresh signal summary yet.";
  }

  const firstLine = reason
    .split("\n")
    .map((line) => line.trim())
    .find(Boolean);

  return firstLine?.replace(/^- /, "") ?? "No fresh signal summary yet.";
}

function calculateDurationHours(openedAt: string): number {
  const openedTime = toTime(openedAt);
  if (openedTime <= 0) {
    return 0;
  }
  return (Date.now() - openedTime) / (1000 * 60 * 60);
}

function toNumber(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
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

function average(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  return sum(values) / values.length;
}
