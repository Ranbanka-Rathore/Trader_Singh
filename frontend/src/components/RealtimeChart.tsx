import { useEffect, useRef } from 'react';
import { createChart, ColorType, AreaSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, AreaData, UTCTimestamp } from 'lightweight-charts';

interface RealtimeChartProps {
  ticker: string;
  initialData: { t: string; p: number }[];
  liveTick?: { t: string; p: number } | null;
}

export const RealtimeChart = ({ ticker, initialData, liveTick }: RealtimeChartProps) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null);
  const lastTimestampRef = useRef<number>(0);

  // 1. Initialize Chart (Once per ticker)
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#6b7280',
        fontFamily: 'JetBrains Mono, monospace',
      },
      grid: {
        vertLines: { color: '#111827' },
        horzLines: { color: '#111827' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 250,
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        borderColor: '#1f2937',
        fixLeftEdge: true,
      },
      rightPriceScale: {
        borderColor: '#1f2937',
        autoScale: true,
      },
    });

    const series = chart.addSeries(AreaSeries, {
      lineColor: '#00f3ff',
      topColor: 'rgba(0, 243, 255, 0.3)',
      bottomColor: 'rgba(0, 243, 255, 0)',
      lineWidth: 2,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
      }
    };
  }, [ticker]); // ONLY re-init on ticker change

  // 2. Load Initial Data (Once when it arrives)
  useEffect(() => {
    if (!seriesRef.current || !initialData || initialData.length === 0) return;
    
    // Only set initial data if series is empty or we haven't set it yet
    const chartData: AreaData[] = [];
    const seenTimes = new Set<number>();
    
    const sortedData = [...initialData].sort((a, b) => new Date(a.t).getTime() - new Date(b.t).getTime());

    for (const d of sortedData) {
      const time = Math.floor(new Date(d.t).getTime() / 1000);
      if (!isNaN(time) && !seenTimes.has(time)) {
        chartData.push({ time: time as UTCTimestamp, value: d.p });
        seenTimes.add(time);
      }
    }
    
    if (chartData.length > 0) {
      seriesRef.current.setData(chartData);
      lastTimestampRef.current = chartData[chartData.length - 1].time as number;
    }
  }, [ticker, initialData.length > 0]); // Load when first batch arrives

  // 3. Handle Live Ticks (Optimized)
  useEffect(() => {
    if (seriesRef.current && liveTick) {
      const time = Math.floor(new Date(liveTick.t).getTime() / 1000);
      if (!isNaN(time) && time >= lastTimestampRef.current) {
        seriesRef.current.update({
          time: time as UTCTimestamp,
          value: liveTick.p,
        });
        lastTimestampRef.current = time;
      }
    }
  }, [liveTick]);

  return (
    <div className="relative w-full">
      <div className="absolute top-2 left-4 z-10 flex items-center space-x-2">
        <div className="w-2 h-2 bg-accent rounded-full animate-pulse"></div>
        <span className="text-[10px] font-bold tracking-widest text-accent uppercase">LIVE {ticker}</span>
      </div>
      <div ref={chartContainerRef} className="w-full" />
    </div>
  );
};
