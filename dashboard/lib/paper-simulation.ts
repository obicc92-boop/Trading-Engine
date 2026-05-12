import type { TradeRecord } from "@/lib/types";


export interface PaperSimulationSnapshot {
  feeRate: number;
  slippageBps: number;
  entryPriceRaw: number | null;
  entryPriceFilled: number | null;
  exitPriceRaw: number | null;
  exitPriceFilled: number | null;
  requestedPositionSize: number | null;
  filledPositionSize: number | null;
  entryFeePaid: number;
  exitFeePaid: number;
  totalFeesPaid: number;
  totalEntryCost: number | null;
  sizeCappedForCash: boolean;
  partialExitTaken: boolean;
  partialRealizedPnl: number;
  partialExitFeePaid: number;
  remainingEntryFeePaid: number;
  model: string | null;
  grossPnl: number | null;
  netPnl: number | null;
}

export function getPaperSimulationSnapshot(trade: TradeRecord): PaperSimulationSnapshot {
  const paperSimulation = extractPaperSimulationRecord(trade);
  const entryFeePaid = readNumber(paperSimulation["entry_fee_paid"]);
  const exitFeePaid = readNumber(paperSimulation["exit_fee_paid"]);

  return {
    feeRate: resolveFeeRate(paperSimulation),
    slippageBps: readNumber(paperSimulation["slippage_bps"]),
    entryPriceRaw: readNullableNumber(paperSimulation["entry_price_raw"]),
    entryPriceFilled: readNullableNumber(paperSimulation["entry_price_filled"]) ?? toNumber(trade.entry),
    exitPriceRaw: readNullableNumber(paperSimulation["exit_price_raw"]),
    exitPriceFilled: readNullableNumber(paperSimulation["exit_price_filled"]) ?? readNullableNumber(trade.exit_price),
    requestedPositionSize: readNullableNumber(paperSimulation["requested_position_size"]),
    filledPositionSize: readNullableNumber(paperSimulation["filled_position_size"]) ?? toNumber(trade.position_size),
    entryFeePaid,
    exitFeePaid,
    totalFeesPaid: readNumber(paperSimulation["total_fees_paid"]) || entryFeePaid + exitFeePaid,
    totalEntryCost: readNullableNumber(paperSimulation["total_entry_cost"]),
    sizeCappedForCash: Boolean(paperSimulation["size_capped_for_cash"]),
    partialExitTaken: Boolean(paperSimulation["partial_exit_taken"]),
    partialRealizedPnl: readNumber(paperSimulation["partial_realized_pnl"]),
    partialExitFeePaid: readNumber(paperSimulation["partial_exit_fee_paid"]),
    remainingEntryFeePaid: readNumber(paperSimulation["remaining_entry_fee_paid"]) || entryFeePaid,
    model: typeof paperSimulation["model"] === "string" ? String(paperSimulation["model"]) : null,
    grossPnl: readNullableNumber(paperSimulation["gross_pnl"]),
    netPnl: readNullableNumber(paperSimulation["net_pnl"]) ?? readNullableNumber(trade.pnl),
  };
}

function extractPaperSimulationRecord(trade: TradeRecord): Record<string, unknown> {
  const metadata = trade.metadata;
  if (!metadata || typeof metadata !== "object") {
    return {};
  }

  const paperSimulation = metadata["paper_simulation"];
  return paperSimulation && typeof paperSimulation === "object" ? (paperSimulation as Record<string, unknown>) : {};
}

function resolveFeeRate(paperSimulation: Record<string, unknown>): number {
  const storedRate = readNumber(paperSimulation["fee_rate"]);
  if (storedRate > 0) {
    return storedRate;
  }

  const fallbackRate = Number(process.env.PAPER_FEE_RATE ?? "0");
  return Number.isFinite(fallbackRate) ? fallbackRate : 0;
}

function readNullableNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function readNumber(value: unknown): number {
  return readNullableNumber(value) ?? 0;
}

function toNumber(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}
