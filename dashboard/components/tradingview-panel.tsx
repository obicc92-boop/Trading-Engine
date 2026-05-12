"use client";

import { useEffect, useRef, useState } from "react";


const INITIAL_LOAD_DELAY_MS = 1200;


export function TradingViewPanel({ symbol }: { symbol: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [isVisible, setIsVisible] = useState(false);
  const [canBootWidget, setCanBootWidget] = useState(false);
  const [widgetReady, setWidgetReady] = useState(false);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setCanBootWidget(true);
    }, INITIAL_LOAD_DELAY_MS);

    return () => {
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    const node = containerRef.current;
    if (!node || isVisible) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setIsVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "240px 0px" }
    );

    observer.observe(node);
    return () => {
      observer.disconnect();
    };
  }, [isVisible]);

  useEffect(() => {
    const containerNode = containerRef.current;

    if (!containerNode || !isVisible || !canBootWidget) {
      return;
    }

    let isCancelled = false;
    containerNode.innerHTML = "";
    setWidgetReady(false);
    setLoadError(false);

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.type = "text/javascript";
    script.async = true;
    script.onload = () => {
      if (!isCancelled) {
        setWidgetReady(true);
      }
    };
    script.onerror = () => {
      if (!isCancelled) {
        setLoadError(true);
      }
    };
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol,
      interval: "60",
      timezone: "Etc/UTC",
      theme: "dark",
      style: "1",
      locale: "en",
      backgroundColor: "rgba(2, 6, 12, 0)",
      hide_top_toolbar: true,
      allow_symbol_change: false,
      save_image: false,
      support_host: "https://www.tradingview.com"
    });

    containerNode.appendChild(script);

    return () => {
      isCancelled = true;
      script.onload = null;
      script.onerror = null;
      containerNode.innerHTML = "";
    };
  }, [canBootWidget, isVisible, symbol]);

  return (
    <div className="relative h-[340px] overflow-hidden rounded-[24px] border border-white/10 bg-slate-950 sm:h-[420px]">
      <div ref={containerRef} className="tradingview-widget-container h-full w-full" />

      {!widgetReady && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-[radial-gradient(circle_at_top,rgba(44,168,163,0.16),transparent_42%),linear-gradient(180deg,rgba(2,6,12,0.92),rgba(7,16,26,0.97))] px-4 text-center sm:px-6">
          <div className="space-y-2">
            <p className="text-xs uppercase tracking-[0.24em] text-cyan-200/70">Live chart</p>
            <h3 className="text-lg font-semibold text-white sm:text-xl">TradingView loads after the dashboard settles.</h3>
            <p className="max-w-md text-sm leading-6 text-white/65">
              The dashboard now prioritizes signals, positions, and performance first so the page feels responsive before the external chart boots.
            </p>
          </div>

          {!loadError ? (
            <button
              type="button"
              onClick={() => setCanBootWidget(true)}
              className="rounded-full border border-cyan-200/30 bg-cyan-300/10 px-4 py-2 text-sm font-medium text-cyan-100 transition hover:bg-cyan-300/16"
            >
              Load chart now
            </button>
          ) : (
            <a
              href={`https://www.tradingview.com/symbols/${symbol.replace("BYBIT:", "")}/`}
              target="_blank"
              rel="noreferrer"
              className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-white transition hover:bg-white/10"
            >
              Open chart in TradingView
            </a>
          )}
        </div>
      )}
    </div>
  );
}
