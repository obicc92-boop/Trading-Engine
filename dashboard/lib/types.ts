export type SignalAction = "BUY" | "SELL" | "HOLD";
export type ExecutionLane = "paper" | "live";

export interface SignalIndicators {
  close?: number;
  ema_20?: number;
  ma_50?: number;
  ma_200?: number;
  rsi_14?: number;
  atr_14?: number;
  atr_pct_14?: number;
  adx_14?: number;
  macd?: number;
  macd_signal?: number;
  macd_diff?: number;
  volume?: number;
  volume_ratio_20?: number;
  highest_high_20?: number;
  lowest_low_20?: number;
  setup_type?: string;
  regime_slug?: string;
  regime_label?: string;
  regime_tradable?: boolean;
  higher_timeframe_aligned?: boolean;
  higher_timeframe_regime_slug?: string;
  higher_timeframe_regime_label?: string;
  higher_timeframe_strength_score?: number;
  symbol_strength_score?: number;
  symbol_rank?: number;
  trade_priority_allowed?: boolean;
}

export interface SignalRecord {
  id?: string | number;
  symbol: string;
  interval: string;
  action: SignalAction;
  confidence: number;
  eligible: boolean;
  execution_intent: string;
  entry: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  position_size: number | null;
  timestamp: string;
  reason?: string;
  rejection_reasons?: string[];
  warnings?: string[];
  indicators?: SignalIndicators;
}

export interface TradeRecord {
  id?: string | number;
  symbol: string;
  interval: string;
  status: string;
  execution_mode: string;
  entry: number;
  stop_loss: number;
  take_profit: number;
  position_size: number;
  confidence?: number;
  risk_reward_ratio?: number;
  notional_value?: number;
  opened_at: string;
  closed_at?: string | null;
  exit_price?: number | null;
  pnl?: number | null;
  pnl_pct?: number | null;
  notes?: string;
  metadata?: Record<string, unknown>;
}

export interface JournalEntryRecord {
  id?: string | number;
  title: string;
  body: string;
  symbol?: string | null;
  related_trade_id?: string | null;
  tags?: string[];
  timestamp: string;
}

export interface PerformanceLogRecord {
  id?: string | number;
  execution_mode: string;
  total_equity: number;
  available_cash: number;
  realized_pnl_today: number;
  total_realized_pnl: number;
  unrealized_pnl: number;
  open_positions: number;
  win_rate: number;
  timestamp: string;
}

export interface PlatformSettings {
  executionMode: string;
  bybitEnvironment: string;
  riskPerTrade: string;
  dailyLossLimit: string;
  confidenceThreshold: string;
  maxOpenTrades: string;
  orderPlacement: string;
  manualConfirmation: string;
  liveRollout: string;
  confirmationTtl: string;
}

export interface DashboardBundle {
  signals: SignalRecord[];
  trades: TradeRecord[];
  journalEntries: JournalEntryRecord[];
  performanceLogs: PerformanceLogRecord[];
  settings: PlatformSettings;
}

export interface OpenTradeMonitorRecord {
  trade: TradeRecord;
  currentPrice: number;
  unrealizedPnl: number;
  unrealizedPct: number;
  entryPriceRaw: number | null;
  entryFeePaid: number;
  estimatedExitFee: number;
  totalFeesIfClosedNow: number;
  simulationSlippageBps: number;
  sizeCappedForCash: boolean;
  distanceToStopPct: number | null;
  distanceToTargetPct: number | null;
  targetProgressPct: number | null;
  durationHours: number;
  regimeLabel: string;
  lastSignalTimestamp: string | null;
  lastSignalAction: SignalAction | null;
  lastSignalConfidence: number | null;
  lastSignalReason: string;
  coachNote: JournalEntryRecord | null;
}

export interface OpenTradeMonitorSummary {
  totalOpenTrades: number;
  totalUnrealizedPnl: number;
  averageUnrealizedPct: number;
  nearestStopLabel: string;
  highestConvictionLabel: string;
  lastUpdated: string | null;
  entries: OpenTradeMonitorRecord[];
}

export interface ReviewBreakdownRow {
  label: string;
  trades: number;
  wins: number;
  losses: number;
  winRate: number;
  totalPnl: number;
  averagePnl: number;
}

export interface ReviewExitBreakdown {
  label: string;
  count: number;
  totalPnl: number;
}

export interface ReviewJournalTagRow {
  tag: string;
  count: number;
}

export interface ReviewTradeDiagnostic {
  id?: string | number;
  symbol: string;
  interval: string;
  setupType: string;
  symbolStrengthScore: number;
  symbolRank: number | null;
  tradePriorityAllowed: boolean;
  positionSize: number;
  exitLabel: string;
  pnl: number;
  pnlPct: number;
  openedAt: string;
  closedAt?: string | null;
  note: string;
  whyItMatters: string;
  entryPriceRaw: number | null;
  entryPriceFilled: number | null;
  exitPriceRaw: number | null;
  exitPriceFilled: number | null;
  entryFeePaid: number;
  exitFeePaid: number;
  totalFeesPaid: number;
  slippageBps: number;
  sizeCappedForCash: boolean;
}

export interface ReviewPeriodSummary {
  label: string;
  startDate: string;
  endDate: string;
  closedTrades: number;
  approvedSignals: number;
  totalSignals: number;
  approvalRate: number;
  totalPnl: number;
  expectancy: number;
  winRate: number;
  maxDrawdownPct: number;
}

export interface ReviewPeriodComparison {
  current: ReviewPeriodSummary;
  previous: ReviewPeriodSummary;
}

export interface ReviewHighlight {
  title: string;
  headline: string;
  symbol?: string;
  interval?: string;
  pnl?: number;
  regimeLabel?: string;
  detail: string;
}

export interface PaperReviewSummary {
  totalSignals: number;
  approvedSignals: number;
  approvalRate: number;
  closedTrades: number;
  openTrades: number;
  totalRealizedPnl: number;
  winRate: number;
  expectancy: number;
  averageWin: number;
  averageLoss: number;
  profitFactor: number | null;
  maxDrawdownValue: number;
  maxDrawdownPct: number;
  averageHoldHours: number;
  currentStreak: number;
  currentStreakLabel: string;
  largestWin: number;
  largestLoss: number;
  coachNotes: number;
  exitBreakdown: ReviewExitBreakdown[];
  symbolBreakdown: ReviewBreakdownRow[];
  intervalBreakdown: ReviewBreakdownRow[];
  regimeBreakdown: ReviewBreakdownRow[];
  setupBreakdown: ReviewBreakdownRow[];
  selectionBreakdown: ReviewBreakdownRow[];
  journalTagBreakdown: ReviewJournalTagRow[];
  flaggedTrades: ReviewTradeDiagnostic[];
  weeklyComparison: ReviewPeriodComparison;
  bestSetup: ReviewHighlight | null;
  worstMistake: ReviewHighlight | null;
  recentReviewNotes: JournalEntryRecord[];
  reviewPrompts: string[];
  lastUpdated: string | null;
}

export interface PairedTradeComparisonRecord {
  tradeGroupId: string;
  symbol: string;
  interval: string;
  paperTrade: TradeRecord | null;
  liveTrade: TradeRecord | null;
  paperStatus: string | null;
  liveStatus: string | null;
  paperEntry: number | null;
  liveEntry: number | null;
  paperExit: number | null;
  liveExit: number | null;
  paperPnl: number | null;
  livePnl: number | null;
  paperFees: number | null;
  liveFees: number | null;
  entryDeltaPct: number | null;
  exitDeltaPct: number | null;
  openedAt: string | null;
  updatedAt: string | null;
  coachNotes: JournalEntryRecord[];
}

export interface PairedTradeComparisonSummary {
  totalPairs: number;
  pairedTrades: number;
  paperOnlyTrades: number;
  liveOnlyTrades: number;
  paperNetPnl: number;
  liveNetPnl: number;
  netPnlDelta: number;
  lastUpdated: string | null;
  entries: PairedTradeComparisonRecord[];
}
