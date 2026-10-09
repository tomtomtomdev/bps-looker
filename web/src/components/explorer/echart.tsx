"use client";

import { LineChart } from "echarts/charts";
import {
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

import type { ChartOption } from "@/lib/explorer/series";

// Tree-shaken ECharts: only what the explorer charts use.
echarts.use([
  LineChart,
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

/** A minimal ECharts host: inits on mount, replaces the option on change, follows its size. */
export function EChart({
  option,
  label,
  className,
}: {
  option: ChartOption;
  label: string;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const instance = echarts.init(el, undefined, { renderer: "canvas" });
    chart.current = instance;
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option as echarts.EChartsCoreOption, { notMerge: true });
  }, [option]);

  // The canvas isn't accessible: the figure's label names the series (values: table view).
  return (
    <figure aria-label={label} className={className}>
      <div ref={ref} className="size-full" />
    </figure>
  );
}
