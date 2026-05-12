"use client";

import dynamic from "next/dynamic";


const TradingViewPanel = dynamic(
  () => import("@/components/tradingview-panel").then((module) => module.TradingViewPanel),
  {
    ssr: false,
    loading: () => <div className="h-[340px] w-full animate-pulse rounded-[24px] bg-white/[0.04] sm:h-[420px]" />
  }
);

export function LazyTradingViewPanel({ symbol }: { symbol: string }) {
  return <TradingViewPanel symbol={symbol} />;
}
