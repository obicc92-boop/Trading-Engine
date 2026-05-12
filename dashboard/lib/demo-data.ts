import type { DashboardBundle } from "@/lib/types";

const now = Date.now();

export const demoData: DashboardBundle = {
  signals: [
    {
      id: "sig-1",
      symbol: "BTCUSDT",
      interval: "1h",
      action: "BUY",
      confidence: 82,
      eligible: true,
      execution_intent: "OPEN_LONG",
      entry: 62000,
      stop_loss: 60500,
      take_profit: 65000,
      position_size: 0.08,
      timestamp: new Date(now - 1000 * 60 * 8).toISOString(),
      reason: "- Price is trading above MA50.\n- RSI is above 50, confirming bullish momentum.\n- MA50 is above MA200, reinforcing the broader uptrend.",
      indicators: {
        close: 62000,
        ma_50: 61240,
        ma_200: 58810,
        rsi_14: 61.8
      }
    },
    {
      id: "sig-2",
      symbol: "ETHUSDT",
      interval: "15m",
      action: "HOLD",
      confidence: 38,
      eligible: false,
      execution_intent: "NO_ACTION",
      entry: 3010,
      stop_loss: null,
      take_profit: null,
      position_size: null,
      timestamp: new Date(now - 1000 * 60 * 22).toISOString(),
      reason: "- Trend and RSI are not aligned, so the setup is neutral.",
      indicators: {
        close: 3010,
        ma_50: 3014,
        ma_200: 2998,
        rsi_14: 49.2
      }
    },
    {
      id: "sig-3",
      symbol: "SOLUSDT",
      interval: "5m",
      action: "SELL",
      confidence: 74,
      eligible: true,
      execution_intent: "CLOSE_LONG",
      entry: 145.2,
      stop_loss: null,
      take_profit: null,
      position_size: null,
      timestamp: new Date(now - 1000 * 60 * 40).toISOString(),
      reason: "- Price is trading below MA50.\n- RSI is below 50, confirming bearish momentum.\n- In spot mode this should be treated as an exit or de-risking signal, not a short sale.",
      indicators: {
        close: 145.2,
        ma_50: 147.1,
        ma_200: 149.4,
        rsi_14: 39.4
      }
    }
  ],
  trades: [
    {
      id: "trade-1",
      symbol: "BTCUSDT",
      interval: "1h",
      status: "OPEN",
      execution_mode: "paper",
      entry: 62000,
      stop_loss: 60500,
      take_profit: 65000,
      position_size: 0.08,
      confidence: 82,
      risk_reward_ratio: 2,
      notional_value: 4960,
      opened_at: new Date(now - 1000 * 60 * 8).toISOString(),
      notes: "Opened automatically in paper trading mode after a qualified setup.",
      metadata: {
        regime: {
          slug: "trend",
          label: "Trend / steady volatility"
        }
      }
    },
    {
      id: "trade-2",
      symbol: "SOLUSDT",
      interval: "15m",
      status: "CLOSED",
      execution_mode: "paper",
      entry: 141.4,
      stop_loss: 137.6,
      take_profit: 149,
      position_size: 13,
      opened_at: new Date(now - 1000 * 60 * 180).toISOString(),
      closed_at: new Date(now - 1000 * 60 * 52).toISOString(),
      exit_price: 148.9,
      pnl: 97.5,
      pnl_pct: 5.3,
      notes: "Paper trade closed because the take profit target was hit.",
      metadata: {
        regime: {
          slug: "trend",
          label: "Trend / steady volatility"
        }
      }
    }
  ],
  journalEntries: [
    {
      id: "journal-1",
      title: "Paper trade opened",
      body: "Opened a paper trade automatically for BTCUSDT on 1h after a qualified BUY setup.",
      symbol: "BTCUSDT",
      tags: ["paper", "entry", "auto", "1h"],
      timestamp: new Date(now - 1000 * 60 * 8).toISOString()
    },
    {
      id: "journal-review-1",
      title: "Review note: BTC trend continuation",
      body: "Reflection:\nStayed patient and let the uptrend confirm before entry.\n\nLesson:\nThe best trades felt obvious only after the risk was already defined.\n\nBefore chart:\nhttps://example.com/btc-before\n\nAfter chart:\nhttps://example.com/btc-after",
      symbol: "BTCUSDT",
      related_trade_id: "trade-1",
      tags: ["review-note", "trend", "execution", "discipline"],
      timestamp: new Date(now - 1000 * 60 * 6).toISOString()
    },
    {
      id: "journal-ai-1",
      title: "AI coach: TRADE",
      body: "BTC remains aligned with the current long-only rules and the risk budget is intact.\n\nWhy:\n- Trend and momentum align for a long setup.\n- No rule-based rejection is active.\n\nChecklist:\n- Respect the existing stop loss.\n- Review whether volatility still matches your comfort.\n\nJournal Prompt:\n- What conditions would make this setup no longer worth holding?",
      symbol: "BTCUSDT",
      tags: ["ai-coach", "anthropic", "trade"],
      timestamp: new Date(now - 1000 * 60 * 7).toISOString()
    },
    {
      id: "journal-2",
      title: "Discipline checkpoint",
      body: "Skipped an ETH setup because confidence stayed below threshold. That kept the system aligned with quality over activity.",
      symbol: "ETHUSDT",
      tags: ["discipline", "filtering"],
      timestamp: new Date(now - 1000 * 60 * 55).toISOString()
    }
  ],
  performanceLogs: [
    {
      id: "perf-1",
      execution_mode: "paper",
      total_equity: 10187.5,
      available_cash: 5240.0,
      realized_pnl_today: 97.5,
      total_realized_pnl: 187.5,
      unrealized_pnl: 42.4,
      open_positions: 1,
      win_rate: 66.7,
      timestamp: new Date(now - 1000 * 60 * 4).toISOString()
    },
    {
      id: "perf-2",
      execution_mode: "paper",
      total_equity: 10120.4,
      available_cash: 5401.1,
      realized_pnl_today: 80.2,
      total_realized_pnl: 170.2,
      unrealized_pnl: 28.2,
      open_positions: 1,
      win_rate: 66.7,
      timestamp: new Date(now - 1000 * 60 * 36).toISOString()
    },
    {
      id: "perf-3",
      execution_mode: "paper",
      total_equity: 10065.0,
      available_cash: 5644.5,
      realized_pnl_today: 55,
      total_realized_pnl: 145,
      unrealized_pnl: 12.8,
      open_positions: 1,
      win_rate: 50,
      timestamp: new Date(now - 1000 * 60 * 64).toISOString()
    }
  ],
  settings: {
    executionMode: "paper",
    bybitEnvironment: "testnet",
    riskPerTrade: "1%",
    dailyLossLimit: "3%",
    confidenceThreshold: "70%",
    maxOpenTrades: "3",
    orderPlacement: "Disabled",
    manualConfirmation: "Required",
    liveRollout: "10% size | max $25 | 1 entry/day",
    confirmationTtl: "15 minutes"
  }
};
