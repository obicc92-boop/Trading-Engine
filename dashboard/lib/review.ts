import type {
  JournalEntryRecord,
  PaperReviewSummary,
  PerformanceLogRecord,
  ReviewBreakdownRow,
  ReviewExitBreakdown,
  ReviewHighlight,
  ReviewJournalTagRow,
  ReviewPeriodSummary,
  ReviewTradeDiagnostic,
  SignalIndicators,
  SignalRecord,
  TradeRecord
} from "@/lib/types";
import { getPaperSimulationSnapshot } from "@/lib/paper-simulation";


const GENERIC_JOURNAL_TAGS = new Set([
  "paper",
  "live",
  "anthropic",
  "ai-coach",
  "review-note"
]);
const TIMEFRAME_TAG_PATTERN = /^\d+[mhdw]$/i;
const DEFAULT_REGIME = {
  slug: "unclassified",
  label: "Unclassified"
};

type ResolvedRegime = {
  slug: string;
  label: string;
};

type ResolvedSetup = {
  setupType: string;
  symbolStrengthScore: number;
  symbolRank: number | null;
  tradePriorityAllowed: boolean;
};

type AnnotatedTrade = TradeRecord & {
  resolvedRegimeSlug: string;
  resolvedRegimeLabel: string;
  resolvedSetupType: string;
  symbolStrengthScore: number;
  symbolRank: number | null;
  tradePriorityAllowed: boolean;
};

export function buildPaperReviewSummary({
  signals,
  trades,
  journalEntries,
  performanceLogs
}: {
  signals: SignalRecord[];
  trades: TradeRecord[];
  journalEntries: JournalEntryRecord[];
  performanceLogs: PerformanceLogRecord[];
}): PaperReviewSummary {
  const paperSignals = [...signals].sort((left, right) => toTime(right.timestamp) - toTime(left.timestamp));
  const paperTrades = trades.filter((trade) => trade.execution_mode === "paper");
  const openTrades = paperTrades.filter((trade) => trade.status === "OPEN");
  const closedTrades = paperTrades
    .filter((trade) => trade.status === "CLOSED")
    .sort((left, right) => toTime(right.closed_at ?? right.opened_at) - toTime(left.closed_at ?? left.opened_at));
  const paperJournalEntries = journalEntries
    .filter(isPaperRelevantJournalEntry)
    .sort((left, right) => toTime(right.timestamp) - toTime(left.timestamp));
  const paperPerformanceLogs = performanceLogs
    .filter((log) => log.execution_mode === "paper")
    .sort((left, right) => toTime(left.timestamp) - toTime(right.timestamp));

  const reviewNotes = paperJournalEntries.filter((entry) => (entry.tags ?? []).includes("review-note")).slice(0, 6);
  const annotatedClosedTrades = closedTrades.map((trade) => annotateTrade(trade, paperSignals));

  const approvedSignals = paperSignals.filter((signal) => signal.eligible).length;
  const totalRealizedPnl = sum(annotatedClosedTrades.map((trade) => toNumber(trade.pnl)));
  const winningTrades = annotatedClosedTrades.filter((trade) => toNumber(trade.pnl) > 0);
  const losingTrades = annotatedClosedTrades.filter((trade) => toNumber(trade.pnl) < 0);
  const averageWin = average(winningTrades.map((trade) => toNumber(trade.pnl)));
  const averageLoss = average(losingTrades.map((trade) => Math.abs(toNumber(trade.pnl))));
  const grossWins = sum(winningTrades.map((trade) => toNumber(trade.pnl)));
  const grossLosses = Math.abs(sum(losingTrades.map((trade) => toNumber(trade.pnl))));
  const maxDrawdown = calculateMaxDrawdown(paperPerformanceLogs);
  const taggedCoachEntries = paperJournalEntries.filter((entry) => (entry.tags ?? []).includes("ai-coach"));
  const currentStreak = calculateCurrentStreak(annotatedClosedTrades);
  const exitBreakdown = buildExitBreakdown(annotatedClosedTrades);
  const symbolBreakdown = buildBreakdown(annotatedClosedTrades, (trade) => trade.symbol);
  const intervalBreakdown = buildBreakdown(annotatedClosedTrades, (trade) => trade.interval);
  const regimeBreakdown = buildBreakdown(annotatedClosedTrades, (trade) => trade.resolvedRegimeLabel);
  const setupBreakdown = buildBreakdown(annotatedClosedTrades, (trade) => formatSetupTypeLabel(trade.resolvedSetupType));
  const selectionBreakdown = buildBreakdown(annotatedClosedTrades, (trade) => (trade.tradePriorityAllowed ? "Top-ranked symbols" : "Watch-only symbols"));
  const lastUpdated = resolveLastUpdatedTimestamp(paperPerformanceLogs, annotatedClosedTrades, paperSignals, paperJournalEntries);
  const weeklyComparison = buildWeeklyComparison({
    signals: paperSignals,
    closedTrades: annotatedClosedTrades,
    performanceLogs: paperPerformanceLogs,
    anchorTimestamp: lastUpdated
  });

  return {
    totalSignals: paperSignals.length,
    approvedSignals,
    approvalRate: percentage(approvedSignals, paperSignals.length),
    closedTrades: annotatedClosedTrades.length,
    openTrades: openTrades.length,
    totalRealizedPnl,
    winRate: percentage(winningTrades.length, annotatedClosedTrades.length),
    expectancy: annotatedClosedTrades.length > 0 ? totalRealizedPnl / annotatedClosedTrades.length : 0,
    averageWin,
    averageLoss,
    profitFactor:
      annotatedClosedTrades.length === 0 ? null : grossLosses === 0 ? (grossWins > 0 ? Number.POSITIVE_INFINITY : null) : grossWins / grossLosses,
    maxDrawdownValue: maxDrawdown.amount,
    maxDrawdownPct: maxDrawdown.pct,
    averageHoldHours: averageHoldHours(annotatedClosedTrades),
    currentStreak: currentStreak.count,
    currentStreakLabel: currentStreak.label,
    largestWin: winningTrades.length > 0 ? Math.max(...winningTrades.map((trade) => toNumber(trade.pnl))) : 0,
    largestLoss: losingTrades.length > 0 ? Math.min(...losingTrades.map((trade) => toNumber(trade.pnl))) : 0,
    coachNotes: taggedCoachEntries.length,
    exitBreakdown,
    symbolBreakdown,
    intervalBreakdown,
    regimeBreakdown,
    setupBreakdown,
    selectionBreakdown,
    journalTagBreakdown: buildJournalTagBreakdown(paperJournalEntries),
    flaggedTrades: buildFlaggedTrades(annotatedClosedTrades),
    weeklyComparison,
    bestSetup: buildBestSetupHighlight(winningTrades),
    worstMistake: buildWorstMistakeHighlight(losingTrades),
    recentReviewNotes: reviewNotes,
    reviewPrompts: buildReviewPrompts({
      closedTrades: annotatedClosedTrades.length,
      expectancy: annotatedClosedTrades.length > 0 ? totalRealizedPnl / annotatedClosedTrades.length : 0,
      approvalRate: percentage(approvedSignals, paperSignals.length),
      maxDrawdownPct: maxDrawdown.pct,
      exitBreakdown,
      strongestLossCluster: regimeBreakdown
        .filter((row) => row.totalPnl < 0)
        .sort((left, right) => left.totalPnl - right.totalPnl)[0],
      weakestSetupCluster: setupBreakdown
        .filter((row) => row.totalPnl < 0)
        .sort((left, right) => left.totalPnl - right.totalPnl)[0]
    }),
    lastUpdated
  };
}

function annotateTrade(trade: TradeRecord, signals: SignalRecord[]): AnnotatedTrade {
  const resolvedRegime = resolveTradeRegime(trade, signals);
  const resolvedSetup = resolveTradeSetup(trade, signals);
  return {
    ...trade,
    resolvedRegimeSlug: resolvedRegime.slug,
    resolvedRegimeLabel: resolvedRegime.label,
    resolvedSetupType: resolvedSetup.setupType,
    symbolStrengthScore: resolvedSetup.symbolStrengthScore,
    symbolRank: resolvedSetup.symbolRank,
    tradePriorityAllowed: resolvedSetup.tradePriorityAllowed
  };
}

function resolveTradeRegime(trade: TradeRecord, signals: SignalRecord[]): ResolvedRegime {
  const metadataRegime = extractMetadataRegime(trade.metadata);
  if (metadataRegime) {
    return metadataRegime;
  }

  const matchingSignal = findClosestSignal(trade, signals);
  if (matchingSignal?.indicators) {
    return inferRegimeFromIndicators(matchingSignal.indicators);
  }

  return DEFAULT_REGIME;
}

function resolveTradeSetup(trade: TradeRecord, signals: SignalRecord[]): ResolvedSetup {
  const metadataSetup = extractMetadataSetup(trade.metadata);
  if (metadataSetup) {
    return metadataSetup;
  }

  const matchingSignal = findClosestSignal(trade, signals);
  if (matchingSignal?.indicators) {
    return inferSetupFromIndicators(matchingSignal.indicators);
  }

  return {
    setupType: "none",
    symbolStrengthScore: 0,
    symbolRank: null,
    tradePriorityAllowed: true
  };
}

function extractMetadataRegime(metadata: Record<string, unknown> | undefined): ResolvedRegime | null {
  if (!metadata) {
    return null;
  }

  const regime = metadata["regime"];
  if (!regime || typeof regime !== "object") {
    return null;
  }

  const regimeRecord = regime as Record<string, unknown>;
  const slug = typeof regimeRecord.slug === "string" ? regimeRecord.slug.trim().toLowerCase() : "";
  const label = typeof regimeRecord.label === "string" ? regimeRecord.label.trim() : "";
  if (!slug || !label) {
    return null;
  }

  return { slug, label };
}

function extractMetadataSetup(metadata: Record<string, unknown> | undefined): ResolvedSetup | null {
  if (!metadata) {
    return null;
  }

  const setup = metadata["setup"] ?? metadata["trade_management"];
  if (!setup || typeof setup !== "object") {
    return null;
  }

  const setupRecord = setup as Record<string, unknown>;
  return {
    setupType: String(setupRecord.type ?? setupRecord.setup_type ?? "none").trim().toLowerCase() || "none",
    symbolStrengthScore: toNumber(setupRecord.symbol_strength_score),
    symbolRank: normalizeRank(setupRecord.symbol_rank),
    tradePriorityAllowed: setupRecord.trade_priority_allowed !== false
  };
}

function findClosestSignal(trade: TradeRecord, signals: SignalRecord[]): SignalRecord | null {
  const openedAt = toTime(trade.opened_at);
  const candidates = signals.filter((signal) => {
    if (signal.symbol !== trade.symbol || signal.interval !== trade.interval) {
      return false;
    }

    const signalTime = toTime(signal.timestamp);
    const timeGap = Math.abs(openedAt - signalTime);
    return timeGap <= 1000 * 60 * 60 * 36;
  });

  if (candidates.length === 0) {
    return null;
  }

  return candidates.sort((left, right) => Math.abs(openedAt - toTime(left.timestamp)) - Math.abs(openedAt - toTime(right.timestamp)))[0] ?? null;
}

function inferRegimeFromIndicators(indicators: SignalIndicators): ResolvedRegime {
  if (typeof indicators.regime_slug === "string" && typeof indicators.regime_label === "string" && indicators.regime_label.trim()) {
    return {
      slug: indicators.regime_slug.trim().toLowerCase(),
      label: indicators.regime_label.trim()
    };
  }

  const closePrice = toNumber(indicators.close);
  const ma50 = toNumber(indicators.ma_50);
  const ma200 = toNumber(indicators.ma_200);
  const rsi = toNumber(indicators.rsi_14);
  const volatilityPct = ma50 > 0 ? (Math.abs(closePrice - ma50) / ma50) * 100 : 0;
  const highVolatility = volatilityPct >= 2.5 || Math.abs(rsi - 50) >= 18;

  if (closePrice > ma50 && ma50 >= ma200 && rsi >= 55) {
    return {
      slug: highVolatility ? "high-volatility-trend" : "trend",
      label: highVolatility ? "Trend / high volatility" : "Trend / steady volatility"
    };
  }
  if (closePrice < ma50 && ma50 <= ma200 && rsi <= 45) {
    return {
      slug: highVolatility ? "selloff" : "pressure",
      label: highVolatility ? "Selloff / high volatility" : "Bearish pressure"
    };
  }
  if (Math.abs(rsi - 50) <= 6) {
    return {
      slug: "range",
      label: "Range / indecision"
    };
  }
  return {
    slug: "transition",
    label: "Transition / mixed tape"
  };
}

function inferSetupFromIndicators(indicators: SignalIndicators): ResolvedSetup {
  return {
    setupType: String(indicators.setup_type ?? "none").trim().toLowerCase() || "none",
    symbolStrengthScore: toNumber(indicators.symbol_strength_score),
    symbolRank: normalizeRank(indicators.symbol_rank),
    tradePriorityAllowed: indicators.trade_priority_allowed !== false
  };
}

function buildExitBreakdown(trades: AnnotatedTrade[]): ReviewExitBreakdown[] {
  const stats = new Map<string, ReviewExitBreakdown>();

  for (const trade of trades) {
    const label = classifyExitReason(trade.notes);
    const existing = stats.get(label) ?? { label, count: 0, totalPnl: 0 };
    existing.count += 1;
    existing.totalPnl += toNumber(trade.pnl);
    stats.set(label, existing);
  }

  return [...stats.values()].sort((left, right) => right.count - left.count || right.totalPnl - left.totalPnl);
}

function buildBreakdown(trades: AnnotatedTrade[], keySelector: (trade: AnnotatedTrade) => string): ReviewBreakdownRow[] {
  const rows = new Map<string, ReviewBreakdownRow>();

  for (const trade of trades) {
    const label = keySelector(trade);
    const pnl = toNumber(trade.pnl);
    const row = rows.get(label) ?? {
      label,
      trades: 0,
      wins: 0,
      losses: 0,
      winRate: 0,
      totalPnl: 0,
      averagePnl: 0
    };

    row.trades += 1;
    row.totalPnl += pnl;
    if (pnl > 0) {
      row.wins += 1;
    } else if (pnl < 0) {
      row.losses += 1;
    }

    rows.set(label, row);
  }

  return [...rows.values()]
    .map((row) => ({
      ...row,
      winRate: percentage(row.wins, row.trades),
      averagePnl: row.trades > 0 ? row.totalPnl / row.trades : 0
    }))
    .sort((left, right) => right.totalPnl - left.totalPnl || right.trades - left.trades);
}

function buildJournalTagBreakdown(entries: JournalEntryRecord[]): ReviewJournalTagRow[] {
  const counts = new Map<string, number>();

  for (const entry of entries) {
    for (const tag of entry.tags ?? []) {
      const normalizedTag = tag.trim().toLowerCase();
      if (!normalizedTag || GENERIC_JOURNAL_TAGS.has(normalizedTag) || TIMEFRAME_TAG_PATTERN.test(normalizedTag)) {
        continue;
      }

      counts.set(normalizedTag, (counts.get(normalizedTag) ?? 0) + 1);
    }
  }

  return [...counts.entries()]
    .map(([tag, count]) => ({ tag, count }))
    .sort((left, right) => right.count - left.count || left.tag.localeCompare(right.tag))
    .slice(0, 10);
}

function buildFlaggedTrades(trades: AnnotatedTrade[]): ReviewTradeDiagnostic[] {
  const sortedTrades = [...trades].sort((left, right) => toNumber(left.pnl) - toNumber(right.pnl));
  const focusTrades = sortedTrades.filter((trade) => toNumber(trade.pnl) < 0).slice(0, 3);
  const fallbackTrades = focusTrades.length > 0 ? focusTrades : sortedTrades.slice(0, 3);

  return fallbackTrades.map((trade) => {
    const exitLabel = classifyExitReason(trade.notes);
    const simulation = getPaperSimulationSnapshot(trade);
    return {
      id: trade.id,
      symbol: trade.symbol,
      interval: trade.interval,
      setupType: formatSetupTypeLabel(trade.resolvedSetupType),
      symbolStrengthScore: trade.symbolStrengthScore,
      symbolRank: trade.symbolRank,
      tradePriorityAllowed: trade.tradePriorityAllowed,
      positionSize: toNumber(trade.position_size),
      exitLabel,
      pnl: toNumber(trade.pnl),
      pnlPct: toNumber(trade.pnl_pct),
      openedAt: trade.opened_at,
      closedAt: trade.closed_at,
      note: trade.notes ?? "",
      whyItMatters: describeTradeDiagnostic(exitLabel, toNumber(trade.pnl), trade.resolvedRegimeLabel, trade.resolvedSetupType),
      entryPriceRaw: simulation.entryPriceRaw,
      entryPriceFilled: simulation.entryPriceFilled,
      exitPriceRaw: simulation.exitPriceRaw,
      exitPriceFilled: simulation.exitPriceFilled,
      entryFeePaid: simulation.entryFeePaid,
      exitFeePaid: simulation.exitFeePaid,
      totalFeesPaid: simulation.totalFeesPaid,
      slippageBps: simulation.slippageBps,
      sizeCappedForCash: simulation.sizeCappedForCash,
    };
  });
}

function buildBestSetupHighlight(trades: AnnotatedTrade[]): ReviewHighlight | null {
  if (trades.length === 0) {
    return null;
  }

  const trade = [...trades].sort((left, right) => toNumber(right.pnl) - toNumber(left.pnl))[0];
  if (!trade) {
    return null;
  }

  return {
    title: "Best setup",
    headline: `${trade.symbol} ${trade.interval} paid the best`,
    symbol: trade.symbol,
    interval: trade.interval,
    pnl: toNumber(trade.pnl),
    regimeLabel: trade.resolvedRegimeLabel,
    detail: `This ${formatSetupTypeLabel(trade.resolvedSetupType).toLowerCase()} resolved through ${classifyExitReason(trade.notes)} in ${trade.resolvedRegimeLabel.toLowerCase()}. Replay it and look for what made the entry feel clean.`
  };
}

function buildWorstMistakeHighlight(trades: AnnotatedTrade[]): ReviewHighlight | null {
  if (trades.length === 0) {
    return null;
  }

  const trade = [...trades].sort((left, right) => toNumber(left.pnl) - toNumber(right.pnl))[0];
  if (!trade) {
    return null;
  }

  return {
    title: "Worst mistake",
    headline: `${trade.symbol} ${trade.interval} hurt the most`,
    symbol: trade.symbol,
    interval: trade.interval,
    pnl: toNumber(trade.pnl),
    regimeLabel: trade.resolvedRegimeLabel,
    detail: describeTradeDiagnostic(classifyExitReason(trade.notes), toNumber(trade.pnl), trade.resolvedRegimeLabel, trade.resolvedSetupType)
  };
}

function buildReviewPrompts({
  closedTrades,
  expectancy,
  approvalRate,
  maxDrawdownPct,
  exitBreakdown,
  strongestLossCluster,
  weakestSetupCluster
}: {
  closedTrades: number;
  expectancy: number;
  approvalRate: number;
  maxDrawdownPct: number;
  exitBreakdown: ReviewExitBreakdown[];
  strongestLossCluster?: ReviewBreakdownRow;
  weakestSetupCluster?: ReviewBreakdownRow;
}): string[] {
  const prompts: string[] = [];
  const stopLossCount = exitBreakdown.find((row) => row.label === "Stop loss")?.count ?? 0;
  const takeProfitCount = exitBreakdown.find((row) => row.label === "Take profit")?.count ?? 0;

  if (closedTrades < 10) {
    prompts.push("Collect a slightly larger paper sample before retuning the strategy. Ten closed trades is the minimum where patterns start becoming trustworthy.");
  }
  if (closedTrades >= 5 && expectancy <= 0) {
    prompts.push("Expectancy is still flat or negative. Review the worst trades before changing indicators so you can tell whether entries, stops, or exits are the real issue.");
  }
  if (maxDrawdownPct >= 3) {
    prompts.push("Max drawdown is pressing against the daily pain threshold. Check whether losing trades are clustering in one regime instead of everywhere.");
  }
  if (stopLossCount >= Math.max(2, takeProfitCount + 1)) {
    prompts.push("Stop losses are firing more often than targets. That usually means entries are early, stops are too tight, or the system is trading chop.");
  }
  if (approvalRate >= 35) {
    prompts.push("A high approval rate can mean the filter is too permissive. Compare approved signals to actual closed-trade quality before trusting the engine to fire often.");
  }
  if (strongestLossCluster) {
    prompts.push(
      `${strongestLossCluster.label} is the biggest paper leak right now. Review those trades together instead of adjusting the whole strategy at once.`
    );
  }
  if (weakestSetupCluster) {
    prompts.push(
      `${weakestSetupCluster.label} is underperforming. Check whether the entry logic is too permissive or whether that setup works only in a narrower regime.`
    );
  }
  if (prompts.length === 0) {
    prompts.push("The paper results look stable enough to keep observing. Stay in paper mode, keep the rules unchanged, and see whether the edge survives more trades.");
  }

  return prompts.slice(0, 5);
}

function buildWeeklyComparison({
  signals,
  closedTrades,
  performanceLogs,
  anchorTimestamp
}: {
  signals: SignalRecord[];
  closedTrades: AnnotatedTrade[];
  performanceLogs: PerformanceLogRecord[];
  anchorTimestamp: string | null;
}) {
  const anchor = anchorTimestamp ? new Date(anchorTimestamp) : new Date();
  const currentStart = startOfUtcWeek(anchor);
  const previousStart = addDays(currentStart, -7);
  const currentEnd = addDays(currentStart, 7);

  return {
    current: buildPeriodSummary({
      label: "This week",
      start: currentStart,
      end: currentEnd,
      signals,
      closedTrades,
      performanceLogs
    }),
    previous: buildPeriodSummary({
      label: "Last week",
      start: previousStart,
      end: currentStart,
      signals,
      closedTrades,
      performanceLogs
    })
  };
}

function buildPeriodSummary({
  label,
  start,
  end,
  signals,
  closedTrades,
  performanceLogs
}: {
  label: string;
  start: Date;
  end: Date;
  signals: SignalRecord[];
  closedTrades: AnnotatedTrade[];
  performanceLogs: PerformanceLogRecord[];
}): ReviewPeriodSummary {
  const filteredSignals = signals.filter((signal) => inRange(signal.timestamp, start, end));
  const filteredTrades = closedTrades.filter((trade) => inRange(trade.closed_at ?? trade.opened_at, start, end));
  const filteredLogs = performanceLogs.filter((log) => inRange(log.timestamp, start, end));
  const totalPnl = sum(filteredTrades.map((trade) => toNumber(trade.pnl)));
  const wins = filteredTrades.filter((trade) => toNumber(trade.pnl) > 0).length;

  return {
    label,
    startDate: start.toISOString(),
    endDate: end.toISOString(),
    closedTrades: filteredTrades.length,
    approvedSignals: filteredSignals.filter((signal) => signal.eligible).length,
    totalSignals: filteredSignals.length,
    approvalRate: percentage(filteredSignals.filter((signal) => signal.eligible).length, filteredSignals.length),
    totalPnl,
    expectancy: filteredTrades.length > 0 ? totalPnl / filteredTrades.length : 0,
    winRate: percentage(wins, filteredTrades.length),
    maxDrawdownPct: calculateMaxDrawdown(filteredLogs).pct
  };
}

function classifyExitReason(notes?: string | null): string {
  const normalized = String(notes ?? "").toLowerCase();
  if (normalized.includes("adaptive trailing stop") || normalized.includes("trailing stop")) {
    return "Trailing stop";
  }
  if (normalized.includes("stop loss")) {
    return "Stop loss";
  }
  if (normalized.includes("stretch target")) {
    return "Adaptive target";
  }
  if (normalized.includes("take profit")) {
    return "Take profit";
  }
  if (normalized.includes("sell exit") || normalized.includes("flipped to sell")) {
    return "Signal flip";
  }
  return "Other";
}

function describeTradeDiagnostic(exitLabel: string, pnl: number, regimeLabel: string, setupType: string): string {
  const setupLabel = formatSetupTypeLabel(setupType).toLowerCase();
  if (exitLabel === "Stop loss") {
    return `The ${setupLabel} invalidated in ${regimeLabel.toLowerCase()} before paying you. Check whether the entry was rushed or whether the stop sat inside normal noise.`;
  }
  if (exitLabel === "Signal flip") {
    return `The ${setupLabel} lost momentum during ${regimeLabel.toLowerCase()} before target. Compare the entry conditions with the later reversal and look for trend weakness you could have spotted earlier.`;
  }
  if (exitLabel === "Trailing stop") {
    return `The ${setupLabel} paid enough to activate trailing protection, then gave some back. Review whether the trail was too tight or whether the market simply completed its move.`;
  }
  if (exitLabel === "Take profit" && pnl < 0) {
    return "The exit label and PnL disagree. This trade is worth checking for a data-quality or execution-record issue.";
  }
  return `Use this ${setupLabel} in ${regimeLabel.toLowerCase()} as a replay candidate. The goal is to understand the setup quality, not just the dollar result.`;
}

function calculateMaxDrawdown(logs: PerformanceLogRecord[]): { amount: number; pct: number } {
  let peak = Number.NEGATIVE_INFINITY;
  let maxDrawdownAmount = 0;
  let maxDrawdownPct = 0;

  for (const log of logs) {
    const equity = toNumber(log.total_equity);
    if (equity > peak) {
      peak = equity;
      continue;
    }

    if (peak <= 0) {
      continue;
    }

    const drawdownAmount = peak - equity;
    const drawdownPct = (drawdownAmount / peak) * 100;
    if (drawdownAmount > maxDrawdownAmount) {
      maxDrawdownAmount = drawdownAmount;
    }
    if (drawdownPct > maxDrawdownPct) {
      maxDrawdownPct = drawdownPct;
    }
  }

  return {
    amount: maxDrawdownAmount,
    pct: maxDrawdownPct
  };
}

function calculateCurrentStreak(trades: AnnotatedTrade[]): { count: number; label: string } {
  const sortedTrades = [...trades].sort((left, right) => toTime(right.closed_at ?? right.opened_at) - toTime(left.closed_at ?? left.opened_at));
  if (sortedTrades.length === 0) {
    return { count: 0, label: "No closed trades yet" };
  }

  const firstPnl = toNumber(sortedTrades[0].pnl);
  const streakKind = firstPnl > 0 ? "win" : firstPnl < 0 ? "loss" : "flat";
  let count = 0;

  for (const trade of sortedTrades) {
    const pnl = toNumber(trade.pnl);
    const tradeKind = pnl > 0 ? "win" : pnl < 0 ? "loss" : "flat";
    if (tradeKind !== streakKind) {
      break;
    }
    count += 1;
  }

  return {
    count,
    label:
      streakKind === "win"
        ? `${count} winning trade${count === 1 ? "" : "s"} in a row`
        : streakKind === "loss"
          ? `${count} losing trade${count === 1 ? "" : "s"} in a row`
          : "Flat streak"
  };
}

function averageHoldHours(trades: AnnotatedTrade[]): number {
  const durations = trades
    .map((trade) => {
      const openedAt = toTime(trade.opened_at);
      const closedAt = toTime(trade.closed_at);
      if (openedAt <= 0 || closedAt <= 0 || closedAt < openedAt) {
        return null;
      }
      return (closedAt - openedAt) / (1000 * 60 * 60);
    })
    .filter((value): value is number => value != null);

  return average(durations);
}

function resolveLastUpdatedTimestamp(
  performanceLogs: PerformanceLogRecord[],
  trades: AnnotatedTrade[],
  signals: SignalRecord[],
  journalEntries: JournalEntryRecord[]
): string | null {
  const timestamps = [
    ...performanceLogs.map((entry) => entry.timestamp),
    ...trades.flatMap((trade) => [trade.closed_at, trade.opened_at]),
    ...signals.map((entry) => entry.timestamp),
    ...journalEntries.map((entry) => entry.timestamp)
  ]
    .filter((value): value is string => Boolean(value))
    .sort((left, right) => toTime(right) - toTime(left));

  return timestamps[0] ?? null;
}

function isPaperRelevantJournalEntry(entry: JournalEntryRecord): boolean {
  const tags = entry.tags ?? [];
  if (tags.includes("live") || tags.includes("live-confirmation")) {
    return false;
  }

  return !entry.title.toLowerCase().startsWith("live ");
}

function startOfUtcWeek(value: Date): Date {
  const start = new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate()));
  const day = start.getUTCDay();
  const diff = day === 0 ? -6 : 1 - day;
  start.setUTCDate(start.getUTCDate() + diff);
  start.setUTCHours(0, 0, 0, 0);
  return start;
}

function addDays(value: Date, days: number): Date {
  const result = new Date(value);
  result.setUTCDate(result.getUTCDate() + days);
  return result;
}

function inRange(value: string | null | undefined, start: Date, end: Date): boolean {
  const timestamp = toTime(value);
  return timestamp >= start.getTime() && timestamp < end.getTime();
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

function normalizeRank(value: unknown): number | null {
  const numeric = toNumber(value);
  return numeric > 0 ? numeric : null;
}

function formatSetupTypeLabel(value: string): string {
  if (value === "breakout") {
    return "Breakout setup";
  }
  if (value === "pullback") {
    return "Pullback setup";
  }
  if (!value || value === "none") {
    return "Unclassified setup";
  }
  return value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function average(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  return sum(values) / values.length;
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function percentage(numerator: number, denominator: number): number {
  if (denominator <= 0) {
    return 0;
  }
  return (numerator / denominator) * 100;
}
