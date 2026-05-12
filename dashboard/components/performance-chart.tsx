"use client";

import { useEffect, useMemo, useRef } from "react";
import { ColorType, type UTCTimestamp, createChart } from "lightweight-charts";

import type { PerformanceLogRecord } from "@/lib/types";


export function PerformanceChart({ logs }: { logs: PerformanceLogRecord[] }) {
  const chartRef = useRef<HTMLDivElement | null>(null);
  const data = useMemo(
    () => {
      const pointsBySecond = new Map<number, number>();

      [...logs]
        .map((log) => ({
          timestampMs: new Date(log.timestamp).getTime(),
          totalEquity: Number(log.total_equity)
        }))
        .filter((log) => Number.isFinite(log.timestampMs) && Number.isFinite(log.totalEquity))
        .sort((left, right) => left.timestampMs - right.timestampMs)
        .forEach((log) => {
          pointsBySecond.set(Math.floor(log.timestampMs / 1000), log.totalEquity);
        });

      return [...pointsBySecond.entries()].map(([time, value]) => ({
        time: time as UTCTimestamp,
        value
      }));
    },
    [logs]
  );

  useEffect(() => {
    if (!chartRef.current) {
      return;
    }

    const chart = createChart(chartRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "rgba(255,255,255,0.65)"
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.05)" },
        horzLines: { color: "rgba(255,255,255,0.05)" }
      },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.08)"
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.08)"
      },
      crosshair: {
        vertLine: { color: "rgba(137, 255, 232, 0.25)" },
        horzLine: { color: "rgba(137, 255, 232, 0.25)" }
      }
    });

    const series = chart.addAreaSeries({
      lineColor: "#82f8e9",
      topColor: "rgba(130,248,233,0.35)",
      bottomColor: "rgba(130,248,233,0.02)"
    });
    series.setData(data);

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({
          width: entry.contentRect.width,
          height: entry.contentRect.width < 640 ? 240 : 320
        });
      }
    });

    resizeObserver.observe(chartRef.current);
    chart.applyOptions({
      width: chartRef.current.clientWidth,
      height: chartRef.current.clientWidth < 640 ? 240 : 320
    });

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [data]);

  return <div ref={chartRef} className="h-60 w-full sm:h-80" />;
}
