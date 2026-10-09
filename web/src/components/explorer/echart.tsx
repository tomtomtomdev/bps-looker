"use client";

import { BarChart, LineChart, MapChart } from "echarts/charts";
import {
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapContinuousComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef, type CSSProperties } from "react";

// Tree-shaken ECharts: only what the explorer charts use.
echarts.use([
  BarChart,
  LineChart,
  MapChart,
  MarkLineComponent,
  VisualMapContinuousComponent,
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

/** Register a GeoJSON map once under `name` (for `type: "map"` series). */
export function registerMap(name: string, geoJson: object) {
  if (!echarts.getMap(name)) {
    echarts.registerMap(name, geoJson as Parameters<typeof echarts.registerMap>[1]);
  }
}

/** What a click hands back: the clicked data item (our items carry extra fields, e.g. vervar). */
export type ChartClick = { data?: unknown; name?: string };

/** A minimal ECharts host: inits on mount, replaces the option on change, follows its size;
 * `onClick` receives clicks on data items (bars, map regions). */
export function EChart({
  option,
  label,
  className,
  style,
  onClick,
}: {
  option: object;
  label: string;
  className?: string;
  style?: CSSProperties;
  onClick?: (params: ChartClick) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const clickRef = useRef(onClick);
  useEffect(() => {
    clickRef.current = onClick;
  }, [onClick]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const instance = echarts.init(el, undefined, { renderer: "canvas" });
    chart.current = instance;
    instance.on("click", (params) => clickRef.current?.(params as ChartClick));
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
    <figure aria-label={label} className={className} style={style}>
      <div ref={ref} className="size-full" />
    </figure>
  );
}
