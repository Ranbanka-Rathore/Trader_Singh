import { useState, useEffect, useRef } from 'react'
import { 
  Activity, 
  Shield, 
  Zap, 
  Target, 
  AlertTriangle,
  Cpu,
  RefreshCw,
  Play,
  FlaskConical,
  TrendingUp,
  Clock,
  ChevronRight,
  Wifi,
  WifiOff,
  X,
  ChevronDown
} from 'lucide-react'
import { 
  useQuery, 
  useMutation, 
  QueryClient,
  QueryClientProvider
} from '@tanstack/react-query'
import { 
  getSignals, 
  getPositions, 
  getStats, 
  getAutopilotStatus,
  getDevMode,
  startAutopilot,
  stopAutopilot,
  toggleDevMode,
  getRecentTicks,
  getTrades,
  runAiAudit,
  getMarketSnapshot,
  getStructuralLevels,
  getStrategyMode,
  setStrategyMode,
  getFirefighting,
  toggleFirefighting,
  getStrategySelectionMode,
  setStrategySelectionMode,
  getLowMarginMode,
  toggleLowMarginMode
} from './api'
import type { TickData, OpenPosition, SignalAudit, Trade } from './api'

import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const getSignalColorClass = (paStatus: string, hasBorder: boolean = true) => {
  const status = (paStatus || "").toUpperCase();
  const bullishTriggers = [
    "VWAP_BULL_CROSS", "SESSION_HIGH_BREAKOUT", "SMC_DEMAND_BOUNCE", 
    "HTF_SUPPORT_BOUNCE", "MTF_DOUBLE_BOTTOM", "VWAP_PULLBACK_LONG",
    "LIQUIDITY_SWEEP_LONG", "VWAP_DEVIATION_LONG", "LOCAL_DOUBLE_BOTTOM_PULLBACK",
    "BULLISH_HH_BREAKOUT", "BULLISH_HL_PULLBACK"
  ];
  const bearishTriggers = [
    "VWAP_BEAR_CROSS", "SESSION_LOW_BREAKDOWN", "SMC_SUPPLY_REJECTION", 
    "HTF_RESISTANCE_REJECTION", "MTF_DOUBLE_TOP", "VWAP_RALLY_SHORT",
    "LIQUIDITY_SWEEP_SHORT", "VWAP_DEVIATION_SHORT", "LOCAL_DOUBLE_TOP_PULLBACK",
    "BEARISH_LL_BREAKDOWN", "BEARISH_LH_PULLBACK"
  ];

  if (bullishTriggers.includes(status) || status.includes("BULL") || status.includes("LONG") || status.includes("BREAKOUT") || status.includes("BOUNCE")) {
    return hasBorder ? "bg-success/20 text-success border border-success/30" : "bg-success/20 text-success";
  }
  if (bearishTriggers.includes(status) || status.includes("BEAR") || status.includes("SHORT") || status.includes("BREAKDOWN") || status.includes("REJECTION")) {
    return hasBorder ? "bg-danger/20 text-danger border border-danger/30" : "bg-danger/20 text-danger";
  }
  return hasBorder ? "bg-gray-800/20 text-gray-400 border border-gray-700/30" : "bg-gray-800 text-gray-400";
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

function AppContent() {
  const [activeTab, setActiveTab] = useState('radar')
  const [selectedTicker] = useState('NIFTY')
  const [liveTick, setLiveTick] = useState<TickData | null>(null)
  const [marketSnapshot, setMarketSnapshot] = useState<any>(null)
  const [structuralLevels, setStructuralLevels] = useState<any>(null)
  const [isWsConnected, setIsWsConnected] = useState(false)
  const [backendError, setBackendError] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  // --- Queries with auto-polling ---
  const { data: signals = [], isLoading: signalsLoading } = useQuery({ 
    queryKey: ['signals'], 
    queryFn: () => getSignals(),
    refetchInterval: 10000, // Poll every 10s
  })

  const { data: positions = [] } = useQuery({ 
    queryKey: ['positions'], 
    queryFn: () => getPositions(),
    refetchInterval: 8000,
  })

  const { data: stats, isError: statsError } = useQuery<import('./api').PortfolioStats>({ 
    queryKey: ['stats'], 
    queryFn: () => getStats(),
    refetchInterval: 5000, // Poll every 5s for fresh risk metrics
  })

  const { data: trades = [] } = useQuery({ 
    queryKey: ['trades'], 
    queryFn: () => getTrades(),
    refetchInterval: 15000,
  })

  // Track backend online/offline state from statsError
  useEffect(() => {
    setBackendError(statsError);
  }, [statsError]);

  const { data: autopilotStatus } = useQuery({ 
    queryKey: ['autopilot-status'], 
    queryFn: () => getAutopilotStatus(),
    refetchInterval: 3000, // Poll every 3s to quickly reflect toggle changes
  })

  const { data: devMode } = useQuery({ 
    queryKey: ['dev-mode'], 
    queryFn: () => getDevMode(),
    refetchInterval: 5000,
  })

  const { data: firefightingMode = false } = useQuery({ 
    queryKey: ['firefighting-mode'], 
    queryFn: () => getFirefighting(),
    refetchInterval: 5000,
  })

  const { data: lowMarginMode = false } = useQuery({ 
    queryKey: ['low-margin-mode'], 
    queryFn: () => getLowMarginMode(),
    refetchInterval: 5000,
  })

  const { data: strategyMode = 'CREDIT_SPREAD' } = useQuery({ 
    queryKey: ['strategy-mode'], 
    queryFn: () => getStrategyMode(),
    refetchInterval: 5000,
  })

  const strategyModeMutation = useMutation({
    mutationFn: (mode: string) => setStrategyMode(mode),
    onSuccess: (_, variables: string) => {
      queryClient.setQueryData(['strategy-mode'], variables)
      queryClient.invalidateQueries({ queryKey: ['strategy-mode'] })
    }
  })

  const { data: strategySelectionMode = 'MANUAL' } = useQuery({ 
    queryKey: ['strategy-selection-mode'], 
    queryFn: () => getStrategySelectionMode(),
    refetchInterval: 5000,
  })

  const strategySelectionToggleMutation = useMutation({
    mutationFn: (mode: string) => setStrategySelectionMode(mode),
    onSuccess: (_, variables: string) => {
      queryClient.setQueryData(['strategy-selection-mode'], variables)
      queryClient.invalidateQueries({ queryKey: ['strategy-selection-mode'] })
      if (variables === 'AUTO') {
        setTimeout(() => queryClient.invalidateQueries({ queryKey: ['strategy-mode'] }), 500)
      }
    }
  })

  const { data: recentTicks = [] } = useQuery({ 
    queryKey: ['ticks', selectedTicker], 
    queryFn: () => getRecentTicks(selectedTicker),
    staleTime: 60000 
  })

  const { data: snapshotData } = useQuery({
    queryKey: ['snapshot', selectedTicker],
    queryFn: () => getMarketSnapshot(selectedTicker),
    refetchInterval: 5000,
  })

  const { data: levelsData } = useQuery({
    queryKey: ['levels', selectedTicker],
    queryFn: () => getStructuralLevels(selectedTicker),
    refetchInterval: 5000,
  })

  // Synchronize snapshot data from query
  useEffect(() => {
    if (snapshotData && Object.keys(snapshotData).length > 0) {
      setMarketSnapshot(snapshotData)
    }
  }, [snapshotData])

  // Synchronize structural levels data from query
  useEffect(() => {
    if (levelsData && Object.keys(levelsData).length > 0) {
      setStructuralLevels(levelsData)
    }
  }, [levelsData])

  // Selected ticker reference and state clearing
  const selectedTickerRef = useRef(selectedTicker)
  useEffect(() => {
    selectedTickerRef.current = selectedTicker
    setLiveTick(null)
    setMarketSnapshot(null)
    setStructuralLevels(null)
  }, [selectedTicker])

  // --- Mutations ---
  const startMutation = useMutation({
    mutationFn: startAutopilot,
    onSuccess: () => {
      // Immediately force a fresh re-fetch to update the toggle
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['autopilot-status'] }), 500)
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['autopilot-status'] }), 1500)
    }
  })

  const stopMutation = useMutation({
    mutationFn: stopAutopilot,
    onSuccess: () => {
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['autopilot-status'] }), 500)
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['autopilot-status'] }), 1500)
    }
  })

  const devToggleMutation = useMutation({
    mutationFn: (status: boolean) => toggleDevMode(status),
    onSuccess: () => {
      // Force multiple re-fetches to reflect the toggle change immediately
      queryClient.invalidateQueries({ queryKey: ['dev-mode'] })
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['dev-mode'] }), 500)
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['dev-mode'] }), 1500)
    }
  })

  const firefightingToggleMutation = useMutation({
    mutationFn: (status: boolean) => toggleFirefighting(status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['firefighting-mode'] })
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['firefighting-mode'] }), 500)
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['firefighting-mode'] }), 1500)
    }
  })

  const lowMarginToggleMutation = useMutation({
    mutationFn: (status: boolean) => toggleLowMarginMode(status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['low-margin-mode'] })
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['low-margin-mode'] }), 500)
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['low-margin-mode'] }), 1500)
    }
  })

  // --- WebSocket with Reconnection & Heartbeat ---
  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
    let heartbeatInterval: ReturnType<typeof setInterval> | null = null;

    const connect = () => {
      console.log("📡 Connecting to WebSocket...");
      try {
        socket = new WebSocket('ws://localhost:8000/ws');
      } catch {
        setIsWsConnected(false);
        reconnectTimeout = setTimeout(connect, 5000);
        return;
      }

      socket.onopen = () => {
        console.log("✅ WebSocket Connected");
        setIsWsConnected(true);
        setBackendError(false);
        heartbeatInterval = setInterval(() => {
          if (socket?.readyState === 1) {
            socket.send(JSON.stringify({ type: 'HEARTBEAT', timestamp: new Date().toISOString() }));
          }
        }, 30000);
      };

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const currentTicker = selectedTickerRef.current;
          if (data.type === 'TICK' && data.ticker === currentTicker) {
            setLiveTick({ t: data.timestamp, p: data.price, v: data.volume, source: data.source });
          }
          if (data.type === 'SNAPSHOT' && data.data.ticker === currentTicker) {
            setMarketSnapshot(data.data);
          }
          if (data.type === 'STRUCTURAL_LEVELS' && data.data.ticker === currentTicker) {
            setStructuralLevels(data.data);
          }
          if (data.type === 'SCAN_RESULTS') {
            queryClient.invalidateQueries({ queryKey: ['signals'] });
          }
          if (data.type === 'TRADES_CLOSED') {
            queryClient.invalidateQueries({ queryKey: ['positions'] });
            queryClient.invalidateQueries({ queryKey: ['stats'] });
          }
        } catch (e) {
          console.error('WS Parse Error:', e);
        }
      };

      socket.onclose = () => {
        console.log("❌ WebSocket Disconnected. Reconnecting...");
        setIsWsConnected(false);
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        reconnectTimeout = setTimeout(connect, 5000);
      };

      wsRef.current = socket;
    };

    connect();

    return () => {
      if (socket) socket.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (heartbeatInterval) clearInterval(heartbeatInterval);
    };
  }, []);

  // --- Helpers ---
  const handleAutopilotToggle = () => {
    if (autopilotStatus?.status === 'ACTIVE') {
      stopMutation.mutate()
    } else {
      startMutation.mutate()
    }
  }

  const handleDevModeToggle = () => {
    devToggleMutation.mutate(!devMode)
  }

  const handleFirefightingToggle = () => {
    firefightingToggleMutation.mutate(!firefightingMode)
  }

  const handleLowMarginToggle = () => {
    lowMarginToggleMutation.mutate(!lowMarginMode)
  }

  const handleStrategyModeChange = (mode: string) => {
    strategyModeMutation.mutate(mode)
  }

  const handleStrategySelectionToggle = () => {
    const nextMode = strategySelectionMode === 'AUTO' ? 'MANUAL' : 'AUTO'
    strategySelectionToggleMutation.mutate(nextMode)
  }

  const formatCurrency = (val: any) => {
    const n = Number(val) || 0
    return Math.abs(n).toLocaleString()
  }

  const formatDelta = (val: any) => {
    const n = Number(val) || 0
    return n.toLocaleString()
  }

  const isDevModeOn = devMode === true
  const isDevPending = devToggleMutation.isPending

  return (
    <div className="flex h-screen bg-bg text-gray-100 font-sans selection:bg-accent/30 overflow-hidden">
      
      {/* ── Backend Offline Overlay ── */}
      {backendError && (
        <div className="fixed inset-x-0 top-0 z-[100] bg-danger text-white py-1 px-4 flex items-center justify-center space-x-3 text-[10px] font-black tracking-widest animate-pulse">
           <AlertTriangle className="w-3 h-3" />
           <span>BACKEND API OFFLINE — DASHBOARD SHOWING CACHED DATA. RUN START.BAT TO RECONNECT.</span>
        </div>
      )}

      {/* Sidebar */}
      <aside className="w-64 border-r border-border bg-card flex flex-col flex-shrink-0 z-50">
        <div className="p-6 border-b border-border">
          <div className="flex items-center space-x-3 mb-2">
            <div className="w-10 h-10 bg-accent rounded-xl flex items-center justify-center shadow-[0_0_20px_rgba(6,182,212,0.4)]">
              <Cpu className="text-bg w-6 h-6" />
            </div>
            <h1 className="text-xl font-black tracking-tighter text-white">TRADER SINGH</h1>
          </div>
          <span className="text-[10px] px-2 py-0.5 bg-accent/10 text-accent border border-accent/30 rounded font-black tracking-widest">V7.0 INSTITUTIONAL</span>
        </div>

        <nav className="flex-1 p-4 space-y-2">
          <button 
            id="nav-radar"
            onClick={() => setActiveTab('radar')}
            className={cn(
              "w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-all",
              activeTab === 'radar' ? "bg-accent/10 text-accent border border-accent/20" : "text-gray-400 hover:text-white hover:bg-white/5"
            )}
          >
            <Activity className="w-5 h-5" />
            <span className="font-semibold">SMC Radar</span>
          </button>
          <button 
            id="nav-positions"
            onClick={() => setActiveTab('positions')}
            className={cn(
              "w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-all",
              activeTab === 'positions' ? "bg-accent/10 text-accent border border-accent/20" : "text-gray-400 hover:text-white hover:bg-white/5"
            )}
          >
            <Target className="w-5 h-5" />
            <span className="font-semibold">War Room</span>
          </button>
          <button 
            id="nav-ledger"
            onClick={() => setActiveTab('ledger')}
            className={cn(
              "w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-all",
              activeTab === 'ledger' ? "bg-accent/10 text-accent border border-accent/20" : "text-gray-400 hover:text-white hover:bg-white/5"
            )}
          >
            <Clock className="w-5 h-5" />
            <span className="font-semibold">Trade Ledger</span>
          </button>
          <button 
            id="nav-signals"
            onClick={() => setActiveTab('signals')}
            className={cn(
              "w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-all",
              activeTab === 'signals' ? "bg-accent/10 text-accent border border-accent/20" : "text-gray-400 hover:text-white hover:bg-white/5"
            )}
          >
            <Zap className="w-5 h-5" />
            <span className="font-semibold">Intelligence</span>
          </button>
          <button 
            id="nav-audit"
            onClick={() => setActiveTab('audit')}
            className={cn(
              "w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-all",
              activeTab === 'audit' ? "bg-accent/10 text-accent border border-accent/20" : "text-gray-400 hover:text-white hover:bg-white/5"
            )}
          >
            <Shield className="w-5 h-5" />
            <span className="font-semibold">Session Alpha Audit</span>
          </button>
        </nav>

        <div className="p-4 mt-auto space-y-3">
           {stats?.kill_reason && (
             <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 mb-2">
                <div className="flex items-center space-x-2 text-danger mb-1">
                  <AlertTriangle className="w-4 h-4" />
                  <span className="text-[10px] font-black uppercase">KILL SWITCH</span>
                </div>
                <p className="text-[10px] text-gray-400 leading-tight">{String(stats.kill_reason)}</p>
             </div>
           )}

           {/* ── System Control Card ── */}
           <div className="p-4 rounded-xl bg-bg border border-border">
              <div className="flex items-center justify-between mb-4">
                <span className="text-xs text-gray-500 uppercase tracking-widest font-bold">Autopilot Core</span>
                <span className={cn(
                  "text-[9px] font-black px-1.5 py-0.5 rounded",
                  autopilotStatus?.status === 'ACTIVE' ? "bg-success/20 text-success border border-success/30" : "bg-gray-800 text-gray-500 border border-gray-700"
                )}>{autopilotStatus?.status || 'OFFLINE'}</span>
              </div>
              
              <div className="flex items-center justify-between mb-2">
                 <div>
                    <p className="text-xs font-bold text-white mb-0.5">System Control</p>
                    <p className="text-[9px] text-gray-500 leading-none">{autopilotStatus?.status === 'ACTIVE' ? 'Trading Enabled' : 'Cycle Paused'}</p>
                 </div>
                 <button 
                   id="autopilot-toggle"
                   disabled={startMutation.isPending || stopMutation.isPending}
                   onClick={handleAutopilotToggle}
                   className={cn(
                     "w-12 h-6 rounded-full relative transition-all duration-300 focus:outline-none focus:ring-2 focus:ring-accent/40 shadow-inner",
                     autopilotStatus?.status === 'ACTIVE' ? "bg-success shadow-[0_0_15px_rgba(34,197,94,0.4)]" : "bg-gray-700"
                   )}
                 >
                    <div className={cn(
                      "absolute top-1 w-4 h-4 bg-white rounded-full transition-all duration-300 flex items-center justify-center",
                      autopilotStatus?.status === 'ACTIVE' ? "right-1" : "left-1"
                    )}>
                      {(startMutation.isPending || stopMutation.isPending) && <RefreshCw className="w-2 h-2 text-gray-400 animate-spin" />}
                    </div>
                 </button>
              </div>
           </div>

           {/* ── Strategy Selector Card ── */}
           <div className="p-4 rounded-xl bg-bg border border-border">
              <div className="flex flex-col space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-gray-500 uppercase tracking-widest font-bold">Execution Strategy</span>
                  <span className="text-[9px] font-black px-1.5 py-0.5 rounded bg-accent/20 text-accent border border-accent/30">
                    {strategyMode === 'CREDIT_SPREAD' ? 'Credit' : 
                     strategyMode === 'DEBIT_SPREAD' ? 'Debit' : 
                     strategyMode === 'CALENDAR_SPREAD' ? 'Calendar' : 
                     strategyMode === 'DIAGONAL_SPREAD' ? 'Diagonal' : 
                     strategyMode === 'COVERED_CALL' ? 'Covered' : 
                     strategyMode === 'CASH_SECURED_PUT' ? 'Put' : 'Neutral'}
                  </span>
                </div>

                {/* Strategy Selection Toggle */}
                <div className="flex items-center justify-between pb-1 border-b border-border/50">
                   <div>
                      <p className="text-xs font-bold text-white mb-0.5">Strategy Selection</p>
                      <p className="text-[9px] text-gray-500 leading-none">
                        {strategySelectionMode === 'AUTO' ? 'Auto Selector (AI)' : 'Manual Override'}
                      </p>
                   </div>
                   <button 
                     id="strategy-selection-toggle"
                     disabled={strategySelectionToggleMutation.isPending}
                     onClick={handleStrategySelectionToggle}
                     className={cn(
                       "w-10 h-5 rounded-full relative transition-all duration-300 focus:outline-none focus:ring-2 focus:ring-accent/40 shadow-inner flex-shrink-0",
                       strategySelectionMode === 'AUTO' ? "bg-accent shadow-[0_0_15px_rgba(30,58,138,0.4)]" : "bg-gray-700"
                     )}
                   >
                      <div className={cn(
                        "absolute top-0.5 w-4 h-4 bg-white rounded-full transition-all duration-300 flex items-center justify-center",
                        strategySelectionMode === 'AUTO' ? "right-0.5" : "left-0.5"
                      )}>
                        {strategySelectionToggleMutation.isPending && <RefreshCw className="w-2 h-2 text-gray-400 animate-spin" />}
                      </div>
                   </button>
                </div>

                {/* Manual strategy selection dropdown */}
                <div className="flex flex-col space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-gray-400 font-semibold">Active Strategy</span>
                    {strategySelectionMode === 'AUTO' && (
                      <span className="text-[8px] text-accent font-black uppercase tracking-wider bg-accent/10 px-1 rounded">Auto Set</span>
                    )}
                  </div>
                  <div className="relative">
                    <select
                      id="strategy-mode-select"
                      value={strategyMode}
                      disabled={strategyModeMutation.isPending || strategySelectionMode === 'AUTO'}
                      onChange={(e) => handleStrategyModeChange(e.target.value)}
                      className="w-full bg-card border border-border rounded-lg pl-3 pr-8 py-2 text-xs font-semibold text-white focus:outline-none focus:ring-2 focus:ring-accent/40 appearance-none cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                      <option value="CREDIT_SPREAD">🛡️ Credit Spreads</option>
                      <option value="DEBIT_SPREAD">⚡ Debit Spreads</option>
                      <option value="CALENDAR_SPREAD">⏳ Calendar Spread</option>
                      <option value="DIAGONAL_SPREAD">⏳ Diagonal Spread</option>
                      <option value="DELTA_NEUTRAL">🤖 Delta Neutral</option>
                      <option value="COVERED_CALL">📈 Covered Call</option>
                      <option value="CASH_SECURED_PUT">🛡️ Cash Secured Put</option>
                    </select>
                    <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 text-gray-500">
                      <ChevronDown className="w-4 h-4" />
                    </div>
                  </div>
                </div>
              </div>
           </div>

          <button 
            id="off-market-toggle"
            disabled={isDevPending}
            onClick={handleDevModeToggle}
            className={cn(
              "w-full flex items-center justify-between px-4 py-3 rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-warning/40 disabled:opacity-60 disabled:cursor-wait",
              isDevModeOn 
                ? "border-warning/50 bg-warning/10 text-warning shadow-[0_0_15px_rgba(245,158,11,0.15)]" 
                : "border-border text-gray-500 hover:text-white hover:border-gray-600"
            )}
            aria-label="Toggle off-market simulation mode"
            title={isDevModeOn ? "Switch to Live Trading (Dhan API)" : "Switch to Paper Trading (Simulated)"}
          >
            <div className="flex items-center space-x-3">
              <FlaskConical className={cn("w-4 h-4", isDevModeOn && "animate-pulse")} />
              <div className="flex flex-col text-left">
                <span className="text-xs font-bold uppercase tracking-widest">Execution Mode</span>
                <span className="text-[9px] text-gray-500 normal-case tracking-normal">
                  {isDevModeOn ? 'Paper Trading (Simulated)' : 'Live Trading (Dhan API)'}
                </span>
              </div>
            </div>
            <div className={cn(
              "w-8 h-4 rounded-full relative transition-colors flex-shrink-0",
              isDevModeOn ? "bg-warning" : "bg-gray-700"
            )}>
              <div className={cn(
                "absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all duration-300 flex items-center justify-center",
                isDevModeOn ? "right-0.5" : "left-0.5"
              )}>
                {isDevPending && <RefreshCw className="w-1.5 h-1.5 text-gray-400 animate-spin" />}
              </div>
            </div>
          </button>

          <button 
            id="firefighting-toggle"
            disabled={firefightingToggleMutation.isPending}
            onClick={handleFirefightingToggle}
            className={cn(
              "w-full flex items-center justify-between px-4 py-3 rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-60 disabled:cursor-wait",
              firefightingMode 
                ? "border-accent/50 bg-accent/10 text-accent shadow-[0_0_15px_rgba(30,58,138,0.15)]" 
                : "border-border text-gray-500 hover:text-white hover:border-gray-600"
            )}
            aria-label="Toggle options firefighting mode"
            title={firefightingMode ? "Firefighting Enabled (Spreads will auto-hedge/roll)" : "Firefighting Disabled (Normal SL/Safeguards active)"}
          >
            <div className="flex items-center space-x-3">
              <Shield className={cn("w-4 h-4", firefightingMode && "animate-pulse")} />
              <div className="flex flex-col text-left">
                <span className="text-xs font-bold uppercase tracking-widest">Options Firefighting</span>
                <span className="text-[9px] text-gray-500 normal-case tracking-normal">
                  {firefightingMode ? 'Firefighting Enabled (Auto-repair)' : 'Firefighting Disabled (Normal SL)'}
                </span>
              </div>
            </div>
            <div className={cn(
              "w-8 h-4 rounded-full relative transition-colors flex-shrink-0",
              firefightingMode ? "bg-accent" : "bg-gray-700"
            )}>
              <div className={cn(
                "absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all duration-300 flex items-center justify-center",
                firefightingMode ? "right-0.5" : "left-0.5"
              )}>
                {firefightingToggleMutation.isPending && <RefreshCw className="w-1.5 h-1.5 text-gray-400 animate-spin" />}
              </div>
            </div>
          </button>

          <button 
            id="low-margin-toggle"
            disabled={lowMarginToggleMutation.isPending}
            onClick={handleLowMarginToggle}
            className={cn(
              "w-full flex items-center justify-between px-4 py-3 rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-60 disabled:cursor-wait",
              lowMarginMode 
                ? "border-accent/50 bg-accent/10 text-accent shadow-[0_0_15px_rgba(30,58,138,0.15)]" 
                : "border-border text-gray-500 hover:text-white hover:border-gray-600"
            )}
            aria-label="Toggle low margin mode"
            title={lowMarginMode ? "Low Margin Mode Active (<50k Spreads Only)" : "Low Margin Mode Disabled (All Playbooks allowed)"}
          >
            <div className="flex items-center space-x-3">
              <Cpu className={cn("w-4 h-4", lowMarginMode && "animate-pulse")} />
              <div className="flex flex-col text-left">
                <span className="text-xs font-bold uppercase tracking-widest">Low Margin Mode</span>
                <span className="text-[9px] text-gray-500 normal-case tracking-normal">
                  {lowMarginMode ? 'Hedged Spreads Only (<₹50k)' : 'All Playbooks (High Margin)'}
                </span>
              </div>
            </div>
            <div className={cn(
              "w-8 h-4 rounded-full relative transition-colors flex-shrink-0",
              lowMarginMode ? "bg-accent" : "bg-gray-700"
            )}>
              <div className={cn(
                "absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all duration-300 flex items-center justify-center",
                lowMarginMode ? "right-0.5" : "left-0.5"
              )}>
                {lowMarginToggleMutation.isPending && <RefreshCw className="w-1.5 h-1.5 text-gray-400 animate-spin" />}
              </div>
            </div>
          </button>

          {/* Connection Status */}
          <div className="flex items-center justify-between px-1">
            <div className="flex items-center space-x-2">
              {isWsConnected 
                ? <Wifi className="w-3 h-3 text-success" />
                : <WifiOff className="w-3 h-3 text-danger animate-pulse" />
              }
              <span className={cn("text-[10px] font-bold uppercase", isWsConnected ? "text-gray-500" : "text-danger")}>
                {isWsConnected ? 'Live Feed' : 'Reconnecting...'}
              </span>
            </div>
            <span className="text-[10px] text-gray-700">
              {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Header Stats Bar */}
        <header className="h-20 border-b border-border bg-card flex items-center px-8 justify-between flex-shrink-0">
          <div className="flex items-center space-x-8">
            <div>
              <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black mb-1">Portfolio VaR</p>
              <div className="flex items-baseline space-x-1">
                <span className="text-xs text-gray-500 font-bold">₹</span>
                <p className={cn(
                  "text-lg font-black tracking-tighter",
                  (Number(stats?.portfolio_var) || 0) > 20000 ? "text-danger" : "text-delta"
                )}>{formatCurrency(stats?.portfolio_var)}</p>
              </div>
            </div>
            <div>
              <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black mb-1">Daily PnL</p>
              <div className="flex items-baseline space-x-1">
                <span className="text-xs text-gray-500 font-bold">₹</span>
                <p className={cn(
                  "text-lg font-black tracking-tighter",
                  (Number(stats?.daily_pnl) || 0) >= 0 ? "text-success" : "text-danger"
                )}>
                  {formatDelta(stats?.daily_pnl)}
                </p>
              </div>
            </div>
            <div>
              <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black mb-1">Net Delta</p>
              <div className="flex items-baseline space-x-1">
                <p className={cn(
                  "text-lg font-black tracking-tighter",
                  (Number(stats?.net_delta) || 0) >= 0 ? "text-success" : "text-warning"
                )}>{(Number(stats?.net_delta) || 0).toFixed(4)}</p>
              </div>
            </div>
            <div>
              <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black mb-1">Net Theta</p>
              <div className="flex items-baseline space-x-1">
                <p className={cn(
                  "text-lg font-black tracking-tighter",
                  (Number(stats?.net_theta) || 0) >= 0 ? "text-success" : "text-warning"
                )}>{(Number(stats?.net_theta) || 0).toFixed(4)}</p>
              </div>
            </div>
            <div>
              <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black mb-1">Net Gamma</p>
              <div className="flex items-baseline space-x-1">
                <p className={cn(
                  "text-lg font-black tracking-tighter",
                  (Number(stats?.net_gamma) || 0) >= 0 ? "text-success" : "text-warning"
                )}>{(Number(stats?.net_gamma) || 0).toFixed(4)}</p>
              </div>
            </div>
            <div>
              <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black mb-1">Net Vega</p>
              <div className="flex items-baseline space-x-1">
                <p className={cn(
                  "text-lg font-black tracking-tighter",
                  (Number(stats?.net_vega) || 0) >= 0 ? "text-success" : "text-warning"
                )}>{(Number(stats?.net_vega) || 0).toFixed(4)}</p>
              </div>
            </div>
            <div className="border-l border-border pl-8">
              <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black mb-1">GEX Delta</p>
              <div className="flex items-baseline space-x-1">
                <p className={cn(
                  "text-lg font-black tracking-tighter",
                  (Number(stats?.total_gex || 0)) >= 0 ? "text-success" : "text-danger"
                )}>{(Number(stats?.total_gex || 0) / 10000000).toFixed(2)}</p>
                <span className="text-[9px] text-gray-500 font-bold">Cr</span>
              </div>
            </div>
            <div>
              <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black mb-1">Total Exposure</p>
              <div className="flex items-baseline space-x-1">
                <span className="text-xs text-gray-500 font-bold">₹</span>
                <p className="text-lg font-black tracking-tighter text-white">{(Number(stats?.total_exposure || 0) / 100000).toFixed(1)}</p>
                <span className="text-[9px] text-gray-500 font-bold">Lakh</span>
              </div>
            </div>
            <div>
              <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black mb-1">Est. Margin Deployed</p>
              <div className="flex items-baseline space-x-1">
                <span className="text-xs text-gray-500 font-bold">₹</span>
                {(() => {
                  const marginVal = Number(stats?.est_margin_deployed) || 0;
                  if (marginVal < 100000) {
                    return (
                      <p className="text-lg font-black tracking-tighter text-white">{marginVal.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
                    );
                  } else {
                    return (
                      <>
                        <p className="text-lg font-black tracking-tighter text-white">{(marginVal / 100000).toFixed(2)}</p>
                        <span className="text-[9px] text-gray-500 font-bold">Lakh</span>
                      </>
                    );
                  }
                })()}
              </div>
            </div>
          </div>

          <div className="flex items-center space-x-4">
             {/* Active Strategy Badge */}
             <div className="px-4 py-2 bg-accent/10 border border-accent/20 rounded-lg flex items-center space-x-2 shadow-[0_0_10px_rgba(30,58,138,0.15)]">
                <span className="text-[9px] text-gray-500 uppercase tracking-widest font-black">Strategy:</span>
                <span className="text-xs font-black text-accent uppercase tracking-widest">
                  {strategyMode === 'CREDIT_SPREAD' ? 'Credit Spread' :
                   strategyMode === 'DEBIT_SPREAD' ? 'Debit Spread' :
                   strategyMode === 'CALENDAR_SPREAD' ? 'Calendar Spread' :
                   strategyMode === 'DIAGONAL_SPREAD' ? 'Diagonal Spread' :
                   strategyMode === 'COVERED_CALL' ? 'Covered Call' :
                   strategyMode === 'CASH_SECURED_PUT' ? 'Cash Secured Put' : 'Delta Neutral'}
                </span>
                {strategySelectionMode === 'AUTO' ? (
                  <span className="px-1.5 py-0.5 bg-accent/20 text-accent text-[8px] font-black rounded uppercase tracking-wider">Auto</span>
                ) : (
                  <span className="px-1.5 py-0.5 bg-warning/20 text-warning text-[8px] font-black rounded uppercase tracking-wider">Manual</span>
                )}
             </div>

             {(liveTick?.source === 'DHAN_LIVE' || autopilotStatus?.source === 'LIVE') ? (
                <div className="px-4 py-2 bg-success/10 border border-success/20 rounded-lg flex items-center space-x-3 shadow-[0_0_10px_rgba(34,197,94,0.1)]">
                   <div className="w-2 h-2 bg-success rounded-full animate-pulse"></div>
                   <span className="text-xs font-black text-success uppercase tracking-widest">Live Exchange Feed</span>
                </div>
             ) : (
                <div className="px-4 py-2 bg-warning/10 border border-warning/20 rounded-lg flex items-center space-x-3">
                   <RefreshCw className={cn("w-4 h-4 text-warning", isWsConnected && "animate-spin")} style={{ animationDuration: '3s' }} />
                   <span className="text-xs font-black text-warning uppercase tracking-widest">Simulated Data</span>
                </div>
             )}
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-8">
           {activeTab === 'radar' && (
             <RadarView
               liveTick={liveTick}
               marketSnapshot={marketSnapshot}
               structuralLevels={structuralLevels}
               recentTicks={recentTicks}
               signals={signals}
               signalsLoading={signalsLoading}
             />
           )}
           {activeTab === 'positions' && <WarRoomView positions={positions} />}
           {activeTab === 'ledger' && <LedgerView trades={trades} />}
           {activeTab === 'signals' && <IntelligenceView signals={signals} signalsLoading={signalsLoading} />}

           {activeTab === 'audit' && <AiAuditorView />}
        </div>
      </main>
    </div>
  )
}

function IntelligenceView({ signals, signalsLoading }: { signals: SignalAudit[]; signalsLoading: boolean }) {
  const [selectedSignal, setSelectedSignal] = useState<SignalAudit | null>(null);
  const safeSignals = Array.isArray(signals) ? signals : [];

  return (
    <div className="space-y-6 animate-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-2xl font-black text-white tracking-tighter uppercase flex items-center space-x-3">
          <Zap className="w-8 h-8 text-warning" />
          <span>Signal Intelligence</span>
        </h2>
        <div className="px-4 py-1 bg-warning/10 border border-warning/20 rounded-full">
           <span className="text-xs font-bold text-warning tracking-widest uppercase">{safeSignals.length} AUDITS LOGGED</span>
        </div>
      </div>

      <section className="bg-card border border-border rounded-2xl overflow-hidden neon-border">
        {safeSignals.length === 0 ? (
          <div className="h-64 flex flex-col items-center justify-center space-y-4">
            {signalsLoading ? (
              <RefreshCw className="w-12 h-12 text-accent animate-spin" />
            ) : (
              <Activity className="w-12 h-12 text-gray-700" />
            )}
            <p className="text-gray-500 font-bold uppercase tracking-widest text-sm">
              {signalsLoading ? "Loading intelligence signals..." : "No intelligence logs found. Scanning universe..."}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-border bg-white/[0.02]">
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Timestamp</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Ticker</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">PA Status</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">PCR/GEX</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">ML Score</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {safeSignals.map((signal: SignalAudit) => (
                  <tr 
                    key={signal.id} 
                    onClick={() => setSelectedSignal(signal)}
                    className="border-b border-border/50 hover:bg-white/[0.01] transition-colors cursor-pointer group"
                  >
                    <td className="px-6 py-4 text-xs font-bold text-gray-400">{new Date(signal.timestamp).toLocaleTimeString()}</td>
                    <td className="px-6 py-4 text-sm font-black text-white group-hover:text-accent transition-colors">{signal.ticker}</td>
                    <td className="px-6 py-4">
                       <span className={cn(
                         "text-[10px] px-2 py-0.5 rounded font-black uppercase tracking-widest",
                         (signal.pa_status || "").includes('BULLISH') || (signal.pa_status || "").includes('BOS') ? "bg-success/20 text-success border border-success/30" : "bg-danger/20 text-danger border border-danger/30"
                       )}>{signal.pa_status}</span>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex flex-col">
                        <span className="text-xs font-bold text-white">PCR: {Number(signal.pcr || 0).toFixed(2)}</span>
                        <span className="text-[10px] text-gray-500">GEX: {(Number(signal.gex_mn || 0) / 1000).toFixed(1)}k</span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                       <div className="flex items-center space-x-2">
                         <div className="w-12 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                            <div 
                              className={cn("h-full", Number(signal.ml_score || 0) > 0.7 ? "bg-success" : "bg-warning")}
                              style={{ width: `${Number(signal.ml_score || 0) * 100}%` }}
                            ></div>
                         </div>
                         <span className="text-xs font-bold text-white">{(Number(signal.ml_score || 0) * 100).toFixed(0)}%</span>
                       </div>
                    </td>
                    <td className="px-6 py-4">
                       <div className={cn(
                         "flex items-center space-x-2 text-[10px] font-black uppercase tracking-widest",
                         signal.committee_verdict === 'APPROVED' ? "text-success" : "text-danger"
                       )}>
                         {signal.committee_verdict === 'APPROVED' ? <Shield className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
                         <span>{signal.committee_verdict}</span>
                       </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Reasoning Modal */}
      {selectedSignal && (
        <div className="fixed inset-0 bg-bg/80 backdrop-blur-md z-50 flex items-center justify-center p-8 overflow-hidden animate-in fade-in duration-300">
           <div className="bg-card border border-border rounded-3xl max-w-3xl w-full max-h-[80vh] flex flex-col shadow-2xl relative overflow-hidden">
              <button 
                onClick={() => setSelectedSignal(null)}
                className="absolute top-6 right-6 p-2 text-gray-500 hover:text-white transition-colors"
              >
                <X className="w-6 h-6" />
              </button>
              
              <div className="p-8 border-b border-border bg-white/[0.02]">
                <div className="flex items-center space-x-4 mb-4">
                   <div className={cn(
                     "px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest",
                     selectedSignal.committee_verdict === 'APPROVED' ? "bg-success/20 text-success" : "bg-danger/20 text-danger"
                   )}>
                     {selectedSignal.committee_verdict}
                   </div>
                   <span className="text-2xl font-black text-white tracking-tighter uppercase">{selectedSignal.ticker} Intelligence Audit</span>
                </div>
                <p className="text-xs text-gray-500 font-bold uppercase tracking-widest">Logged at: {new Date(selectedSignal.timestamp).toLocaleString()}</p>
              </div>

              <div className="p-8 overflow-y-auto space-y-6">
                <div>
                   <h4 className="text-xs font-black text-accent uppercase tracking-widest mb-4">Committee Reasoning & Debate</h4>
                   <div className="bg-bg rounded-2xl p-6 border border-border/50 text-sm text-gray-300 leading-relaxed font-mono whitespace-pre-wrap">
                      {selectedSignal.committee_reasoning || "Reasoning was bypassed due to high ML confidence (>85%) or fallback rules."}
                   </div>
                </div>

                <div className="grid grid-cols-2 gap-6">
                   <div className="bg-white/[0.02] border border-border/30 rounded-xl p-4">
                      <p className="text-[10px] text-gray-500 font-black uppercase mb-1">Neural Network Score</p>
                      <p className="text-lg font-black text-white">{(Number(selectedSignal.ml_score) * 100).toFixed(1)}% Win Prob</p>
                   </div>
                   <div className="bg-white/[0.02] border border-border/30 rounded-xl p-4">
                      <p className="text-[10px] text-gray-500 font-black uppercase mb-1">Smart Money PCR</p>
                      <p className="text-lg font-black text-white">{Number(selectedSignal.pcr).toFixed(2)}</p>
                   </div>
                </div>
              </div>
           </div>
        </div>
      )}
    </div>
  )
}

function RadarView({ liveTick, marketSnapshot, structuralLevels, recentTicks, signals, signalsLoading }: {
  liveTick: TickData | null;
  marketSnapshot: any;
  structuralLevels: any;
  recentTicks: TickData[];
  signals: SignalAudit[];
  signalsLoading: boolean;
}) {
  const safeTicks = Array.isArray(recentTicks) ? recentTicks : [];
  const safeSignals = Array.isArray(signals) ? signals : [];
  const currentLivePrice = liveTick?.p ? Number(liveTick.p) : (marketSnapshot?.price || (structuralLevels?.price || 0));

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left: Live Ticker HUD */}
        <div className="bg-card border border-border rounded-2xl p-6 neon-border flex flex-col space-y-6">
          <div>
            <h3 className="text-xs font-black text-accent uppercase tracking-widest mb-4 flex items-center space-x-2">
               <Activity className="w-4 h-4" />
               <span>Live Ticker Quote</span>
            </h3>
            
            <div className="space-y-4">
              {/* Ticker Selector */}
              <div className="flex flex-col space-y-2">
                <label className="text-[10px] text-gray-500 font-black uppercase tracking-widest">Active Instrument</label>
                <div className="bg-bg border border-border text-white text-lg font-black px-4 py-2.5 rounded-xl w-full flex items-center justify-between">
                  <span>NIFTY 50</span>
                  <span className="text-[10px] px-2 py-0.5 bg-accent/15 text-accent border border-accent/30 rounded font-black tracking-widest">ACTIVE</span>
                </div>
              </div>

              {/* Massive Live Price Display */}
              <div className="p-5 bg-bg rounded-2xl border border-border/80 flex flex-col justify-center items-center text-center relative overflow-hidden group">
                <div className="absolute top-2 right-3 flex items-center space-x-1">
                  <span className="w-2 h-2 rounded-full bg-success animate-pulse"></span>
                  <span className="text-[8px] text-gray-500 font-bold tracking-widest uppercase">LIVE</span>
                </div>
                <span className="text-[9px] text-gray-500 font-black uppercase tracking-widest mb-1">Last Traded Price</span>
                <span className="text-3xl font-black text-white tracking-tighter glow-text">
                  ₹{(liveTick?.p ? Number(liveTick.p) : (Number(safeTicks[safeTicks.length-1]?.p) || 0)).toLocaleString()}
                </span>
                
                <span className={cn(
                  "text-xs font-bold mt-1 px-2.5 py-0.5 rounded-full",
                  (Number(liveTick?.p) || 0) >= (Number(safeTicks[0]?.p) || 0) ? "text-success bg-success/10" : "text-danger bg-danger/10"
                )}>
                  {((Number(liveTick?.p) || 0) - (Number(safeTicks[0]?.p) || 0)) >= 0 ? '+' : ''}
                  {((Number(liveTick?.p) || 0) - (Number(safeTicks[0]?.p) || 0)).toFixed(2)}
                </span>
              </div>

              {/* Market Snapshot Stats */}
              {marketSnapshot && (
                <div className="grid grid-cols-2 gap-4">
                  <div className="p-4 bg-bg rounded-xl border border-border flex flex-col justify-between">
                    <span className="text-[9px] text-gray-500 font-black uppercase tracking-widest mb-1">Live PCR</span>
                    <span className="text-lg font-black text-delta">{marketSnapshot.pcr || marketSnapshot.coi_pcr}</span>
                  </div>
                  <div className="p-4 bg-bg rounded-xl border border-border flex flex-col justify-between">
                    <span className="text-[9px] text-gray-500 font-black uppercase tracking-widest mb-1">Market Bias</span>
                    <span className={cn(
                      "text-xs font-black px-2 py-1 rounded-lg text-center uppercase tracking-tighter mt-1",
                      marketSnapshot.bias === 'BULLISH' ? "bg-success/20 text-success border border-success/30" : 
                      marketSnapshot.bias === 'BEARISH' ? "bg-danger/20 text-danger border border-danger/30" : 
                      "bg-gray-800 text-gray-500 border border-gray-700"
                    )}>
                      {marketSnapshot.bias || 'NEUTRAL'}
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right: Structural Intelligence (Absolute Transparency) */}
        <div className="lg:col-span-2 bg-card border border-border rounded-2xl p-6 flex flex-col space-y-6">
           <div>
              <h3 className="text-xs font-black text-accent uppercase tracking-widest mb-4 flex items-center space-x-2">
                 <Activity className="w-4 h-4" />
                 <span>Structural Intelligence</span>
              </h3>
              
              {structuralLevels ? (
                 <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Column 1: Core Signals & Bias */}
                    <div className="space-y-4">
                      {/* Primary Bias */}
                      <div className="p-3 bg-bg rounded-xl border border-border flex items-center justify-between">
                         <span className="text-[10px] text-gray-500 font-black uppercase">PA STATUS</span>
                         <span className={cn(
                           "text-xs font-black px-2 py-0.5 rounded",
                           getSignalColorClass(structuralLevels.pa_status, false)
                         )}>{structuralLevels.pa_status}</span>
                      </div>

                      {/* VWAP Signal */}
                      <div className="p-3 bg-bg rounded-xl border border-border flex items-center justify-between">
                         <span className="text-[10px] text-gray-500 font-black uppercase">VWAP SIGNAL</span>
                         <div className="flex items-center space-x-2">
                            <span className="text-xs font-bold text-white">₹{structuralLevels.vwap}</span>
                            <span className={cn(
                              "text-[10px] font-black px-1.5 py-0.5 rounded",
                              currentLivePrice > structuralLevels.vwap ? "text-success bg-success/10" : "text-danger bg-danger/10"
                            )}>{currentLivePrice > structuralLevels.vwap ? 'ABOVE' : 'BELOW'}</span>
                         </div>
                      </div>

                      {/* Order Book Imbalance (OBI) */}
                      <div className="p-3 bg-bg rounded-xl border border-border flex items-center justify-between">
                         <span className="text-[10px] text-gray-500 font-black uppercase">ORDER BOOK IMBALANCE (OBI)</span>
                         <div className="flex items-center space-x-2">
                            <span className="text-xs font-bold text-white">
                              {(structuralLevels.obi !== undefined && structuralLevels.obi !== null) 
                                ? (structuralLevels.obi > 0 ? '+' : '') + Number(structuralLevels.obi).toFixed(4) 
                                : '0.0000'}
                            </span>
                            <span className={cn(
                              "text-[10px] font-black px-1.5 py-0.5 rounded",
                              (structuralLevels.obi !== undefined && structuralLevels.obi !== null) && Number(structuralLevels.obi) > 0.15 ? "text-success bg-success/10" :
                              (structuralLevels.obi !== undefined && structuralLevels.obi !== null) && Number(structuralLevels.obi) < -0.15 ? "text-danger bg-danger/10" :
                              "text-gray-400 bg-gray-800"
                            )}>
                              {(structuralLevels.obi !== undefined && structuralLevels.obi !== null) && Number(structuralLevels.obi) > 0.15 ? 'BUY IMBALANCE' :
                               (structuralLevels.obi !== undefined && structuralLevels.obi !== null) && Number(structuralLevels.obi) < -0.15 ? 'SELL IMBALANCE' : 'BALANCED'}
                            </span>
                         </div>
                      </div>

                      {/* HTF Levels */}
                      <div className="grid grid-cols-2 gap-3">
                         <div className="p-3 bg-bg rounded-xl border border-border">
                            <p className="text-[9px] text-gray-500 font-black uppercase mb-1">PD HIGH</p>
                            <p className="text-sm font-black text-white">₹{structuralLevels.pdh || 'N/A'}</p>
                         </div>
                         <div className="p-3 bg-bg rounded-xl border border-border">
                            <p className="text-[9px] text-gray-500 font-black uppercase mb-1">PD LOW</p>
                            <p className="text-sm font-black text-white">₹{structuralLevels.pdl || 'N/A'}</p>
                         </div>
                         <div className="p-3 bg-bg rounded-xl border border-border col-span-2">
                            <div className="flex justify-between items-center">
                               <p className="text-[9px] text-gray-500 font-black uppercase">SESSION POC</p>
                               <span className="text-[9px] font-black text-warning bg-warning/10 px-1.5 rounded">MAGNET</span>
                            </div>
                            <p className="text-sm font-black text-white">₹{structuralLevels.poc || 'N/A'}</p>
                         </div>
                      </div>
                    </div>

                    {/* Column 2: Gamma Wall & Order Blocks */}
                    <div className="space-y-4">
                      {/* Gamma Transparency */}
                      {marketSnapshot && marketSnapshot.gamma_flip > 0 ? (
                         <div className="p-4 bg-accent/5 rounded-xl border border-accent/20">
                            <p className="text-[10px] text-accent font-black uppercase tracking-widest mb-3">Institutional Gamma</p>
                            <div className="grid grid-cols-1 gap-2">
                               <div className="flex justify-between items-center text-[10px]">
                                  <span className="text-gray-500 font-black uppercase">GAMMA FLIP</span>
                                  <span className="text-white font-black">₹{marketSnapshot.gamma_flip}</span>
                               </div>
                               <div className="flex justify-between items-center text-[10px]">
                                  <span className="text-gray-500 font-black uppercase">CALL WALL</span>
                                  <span className="text-success font-black">₹{marketSnapshot.call_wall}</span>
                               </div>
                               <div className="flex justify-between items-center text-[10px]">
                                  <span className="text-gray-500 font-black uppercase">PUT WALL</span>
                                  <span className="text-danger font-black">₹{marketSnapshot.put_wall}</span>
                               </div>
                            </div>
                         </div>
                      ) : (
                         <div className="p-4 bg-white/[0.01] rounded-xl border border-border/50 flex flex-col justify-center items-center text-center h-[116px]">
                            <p className="text-[10px] text-gray-500 font-black uppercase">Institutional Gamma</p>
                            <p className="text-xs text-gray-600 font-bold mt-1">No live Option Chain data</p>
                         </div>
                      )}

                      {/* Order Blocks */}
                      <div>
                         <div className="flex justify-between items-center mb-3">
                            <p className="text-[10px] text-gray-500 font-black uppercase tracking-widest">Live Order Blocks (S/D)</p>
                            {structuralLevels.momentum !== "NONE" && (
                              <span className={cn(
                                "text-[8px] font-black px-1.5 py-0.5 rounded animate-pulse",
                                structuralLevels.momentum === 'BULLISH' ? "bg-success text-white" : "bg-danger text-white"
                              )}>MOMENTUM_EXPANSION</span>
                            )}
                         </div>
                         <div className="space-y-2">
                            {structuralLevels.order_blocks && structuralLevels.order_blocks.length > 0 ? (
                              structuralLevels.order_blocks.map((ob: any, idx: number) => (
                                <div key={idx} className={cn(
                                  "p-2 rounded-lg border flex items-center justify-between text-[10px]",
                                  ob.type === 'SUPPLY' ? "bg-danger/5 border-danger/20 text-danger" : "bg-success/5 border-success/20 text-success"
                                )}>
                                   <span className="font-black">{ob.type}</span>
                                   <span className="font-bold">₹{ob.bottom.toFixed(0)} - ₹{ob.top.toFixed(0)}</span>
                                   <span className="px-1.5 bg-white/10 rounded font-black">S:{ob.strength}</span>
                                </div>
                              ))
                            ) : (
                              <p className="text-[10px] text-gray-600 italic">No structural blocks detected.</p>
                            )}
                         </div>
                      </div>
                    </div>
                 </div>
              ) : (
                 <div className="h-full flex flex-col items-center justify-center text-center space-y-3 py-12">
                    <Shield className="w-8 h-8 text-gray-800" />
                    <p className="text-[10px] text-gray-600 font-bold uppercase">Awaiting Structural Scan...</p>
                 </div>
              )}
           </div>
        </div>
      </section>

      <section className="bg-card border border-border rounded-2xl overflow-hidden neon-border">
        <div className="px-6 py-4 border-b border-border flex items-center justify-between">
          <h3 className="font-bold text-white flex items-center space-x-2">
            <Zap className="w-4 h-4 text-warning" />
            <span>SMC SIGNAL STREAM</span>
          </h3>
          <div className="flex items-center space-x-3">
            {signalsLoading && <RefreshCw className="w-3 h-3 text-accent animate-spin" />}
            <span className="text-[10px] font-bold text-gray-500 uppercase tracking-widest">{safeSignals.length} SIGNALS CACHED</span>
          </div>
        </div>
        
        {safeSignals.length === 0 ? (
          <div className="h-64 flex flex-col items-center justify-center space-y-4">
            <Activity className="w-12 h-12 text-gray-700" />
            <p className="text-gray-500 font-bold uppercase tracking-widest text-sm">No signals yet. Start autopilot to begin scanning.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-border bg-white/[0.02]">
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Timestamp</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Ticker</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">PA Status</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">PCR/GEX</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">ML Score</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {safeSignals.slice(0, 10).map((signal: SignalAudit) => (
                  <tr 
                    key={signal.id} 
                    className="border-b border-border/50 hover:bg-white/[0.01] transition-colors cursor-pointer group"
                    title={signal.committee_reasoning || "No reasoning provided."}
                  >
                    <td className="px-6 py-4 text-xs font-bold text-gray-400">{new Date(signal.timestamp).toLocaleTimeString()}</td>
                    <td className="px-6 py-4 text-sm font-black text-white group-hover:text-accent transition-colors">{signal.ticker}</td>
                    <td className="px-6 py-4">
                       <span className={cn(
                         "text-[10px] px-2 py-0.5 rounded font-black uppercase tracking-widest",
                         (signal.pa_status || "").includes('BULLISH') || (signal.pa_status || "").includes('BOS') ? "bg-success/20 text-success border border-success/30" : "bg-danger/20 text-danger border border-danger/30"
                       )}>{signal.pa_status}</span>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex flex-col">
                        <span className="text-xs font-bold text-white">PCR: {Number(signal.pcr || 0).toFixed(2)}</span>
                        <span className="text-[10px] text-gray-500">GEX: {(Number(signal.gex_mn || 0) / 1000).toFixed(1)}k</span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                       <div className="flex items-center space-x-2">
                         <div className="w-12 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                            <div 
                              className={cn("h-full", Number(signal.ml_score || 0) > 0.7 ? "bg-success" : "bg-warning")}
                              style={{ width: `${Number(signal.ml_score || 0) * 100}%` }}
                            ></div>
                         </div>
                         <span className="text-xs font-bold text-white">{(Number(signal.ml_score || 0) * 100).toFixed(0)}%</span>
                       </div>
                    </td>
                    <td className="px-6 py-4">
                       <div className={cn(
                         "flex items-center space-x-2 text-[10px] font-black uppercase tracking-widest",
                         signal.committee_verdict === 'APPROVED' ? "text-success" : "text-danger"
                       )}>
                         {signal.committee_verdict === 'APPROVED' ? <Shield className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
                         <span>{signal.committee_verdict}</span>
                       </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function LedgerView({ trades }: { trades: Trade[] }) {
  const safeTrades = Array.isArray(trades) ? trades : [];

  return (
    <div className="space-y-6 animate-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-2xl font-black text-white tracking-tighter uppercase flex items-center space-x-3">
          <Clock className="w-8 h-8 text-accent" />
          <span>Trade Ledger</span>
        </h2>
        <div className="px-4 py-1 bg-accent/10 border border-accent/20 rounded-full">
           <span className="text-xs font-bold text-accent tracking-widest uppercase">{safeTrades.length} TRADES LOGGED</span>
        </div>
      </div>

      <section className="bg-card border border-border rounded-2xl overflow-hidden neon-border">
        {safeTrades.length === 0 ? (
          <div className="h-64 flex flex-col items-center justify-center space-y-4">
            <Clock className="w-12 h-12 text-gray-700" />
            <p className="text-gray-500 font-bold uppercase tracking-widest text-sm">No historical trades found in the ledger.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-border bg-white/[0.02]">
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Entry Date</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Ticker</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Strategy</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Entry / Exit</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">PnL</th>
                  <th className="px-6 py-4 text-[10px] font-black text-gray-500 uppercase tracking-widest">Exit Reason</th>
                </tr>
              </thead>
              <tbody>
                {safeTrades.map((trade) => (
                  <tr key={trade.id} className="border-b border-border/50 hover:bg-white/[0.01] transition-colors group">
                    <td className="px-6 py-4">
                       <div className="flex flex-col">
                          <span className="text-xs font-bold text-white">{new Date(trade.entry_date).toLocaleDateString()}</span>
                          <span className="text-[10px] text-gray-500">{new Date(trade.entry_date).toLocaleTimeString()}</span>
                       </div>
                    </td>
                    <td className="px-6 py-4 text-sm font-black text-white">{trade.ticker}</td>
                    <td className="px-6 py-4">
                       <span className="text-[10px] px-2 py-0.5 rounded bg-bg border border-border text-gray-400 font-bold uppercase">
                         {trade.strategy_type}
                       </span>
                    </td>
                    <td className="px-6 py-4">
                       <div className="flex flex-col">
                          <span className="text-xs font-bold text-gray-400">IN: ₹{Number(trade.entry_spot_price).toLocaleString()}</span>
                          <span className="text-xs font-bold text-white">OUT: ₹{Number(trade.exit_price).toLocaleString()}</span>
                       </div>
                    </td>
                    <td className="px-6 py-4">
                       <p className={cn(
                         "text-sm font-black",
                         Number(trade.realized_pnl) >= 0 ? "text-success" : "text-danger"
                       )}>
                         {Number(trade.realized_pnl) >= 0 ? '+' : ''}₹{Number(trade.realized_pnl).toLocaleString()}
                       </p>
                    </td>
                    <td className="px-6 py-4">
                       <span className="text-[10px] font-bold text-gray-500 uppercase tracking-tight italic">
                         {trade.exit_reason}
                       </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function WarRoomView({ positions }: { positions: OpenPosition[] }) {
  const safePositions = Array.isArray(positions) ? positions : [];

  return (
    <div className="space-y-6 animate-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-2xl font-black text-white tracking-tighter uppercase flex items-center space-x-3">
          <Target className="w-8 h-8 text-accent" />
          <span>Active Operations</span>
        </h2>
        <div className="px-4 py-1 bg-accent/10 border border-accent/20 rounded-full">
           <span className="text-xs font-bold text-accent tracking-widest uppercase">{safePositions.length} OPEN TARGETS</span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        {safePositions.map((pos) => (
          <div key={pos.id} className="bg-card border border-border rounded-2xl p-6 hover:border-accent/40 transition-all cursor-pointer group">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-bg rounded-lg">
                   <TrendingUp className="w-5 h-5 text-success" />
                </div>
                <div>
                   <h3 className="text-lg font-black text-white">{pos.ticker}</h3>
                   <span className="text-[10px] font-bold text-gray-500 uppercase tracking-widest">{pos.strategy_type}</span>
                </div>
              </div>
              <div className="text-right">
                <p className="text-xs text-gray-500 font-bold uppercase">Running P&L</p>
                <p className={cn(
                  "text-sm font-black",
                  (pos.unrealized_pnl ?? 0) > 0 ? "text-success" : (pos.unrealized_pnl ?? 0) < 0 ? "text-danger" : "text-white"
                )}>
                  {pos.unrealized_pnl !== undefined
                    ? `${pos.unrealized_pnl >= 0 ? '+' : ''}₹${pos.unrealized_pnl.toLocaleString()}`
                    : '—'}
                </p>
                {pos.unrealized_pnl_source === 'HEURISTIC_FALLBACK' && (
                  <p className="text-[9px] text-gray-600 font-bold uppercase">est.</p>
                )}
              </div>
            </div>

            <div className="grid grid-cols-3 gap-4 py-4 border-y border-border/50 mb-4">
               <div>
                  <p className="text-[10px] text-gray-500 font-black uppercase mb-1">Delta</p>
                  <p className="text-sm font-bold text-delta">{Number(pos.net_delta || 0).toFixed(3)}</p>
               </div>
               <div>
                  <p className="text-[10px] text-gray-500 font-black uppercase mb-1">Theta</p>
                  <p className="text-sm font-bold text-theta">{Number(pos.net_theta || 0).toFixed(3)}</p>
               </div>
               <div>
                  <p className="text-[10px] text-gray-500 font-black uppercase mb-1">Lots</p>
                  <p className="text-sm font-bold text-white">{pos.lots_sized}</p>
               </div>
            </div>

            <div className="flex items-center justify-between">
               <div className="flex items-center space-x-2">
                  <Clock className="w-3 h-3 text-gray-500" />
                  <span className="text-[10px] font-bold text-gray-500">{new Date(pos.entry_date).toLocaleTimeString()}</span>
               </div>
               <button className="flex items-center space-x-1 text-xs font-black text-accent uppercase hover:translate-x-1 transition-transform">
                  <span>SQUARE OFF</span>
                  <ChevronRight className="w-4 h-4" />
               </button>
            </div>
          </div>
        ))}

        {safePositions.length === 0 && (
          <div className="col-span-full h-64 border-2 border-dashed border-border rounded-2xl flex flex-col items-center justify-center space-y-4">
             <Target className="w-12 h-12 text-gray-700" />
             <p className="text-gray-500 font-bold uppercase tracking-widest">No active positions in the field.</p>
          </div>
        )}
      </div>
    </div>
  )
}

function parseMarkdownTable(tableText: string) {
  const lines = tableText.split('\n').map(l => l.trim()).filter(l => l.startsWith('|'));
  if (lines.length < 2) return null;
  
  // Extract headers
  const headers = lines[0].split('|').map(h => h.trim()).filter(Boolean);
  
  // Extract rows
  const rows: string[][] = [];
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.includes('---') || line.includes(':---')) {
      continue;
    }
    const cells = line.split('|').map(c => c.trim().replace(/\*\*/g, ''));
    if (cells.length >= 2) {
      cells.shift(); // remove empty first element
      cells.pop();   // remove empty last element
      rows.push(cells);
    }
  }
  return { headers, rows };
}

function renderReportSections(report: string) {
  const lines = report.split('\n');
  const sections: { title: string; content: string[] }[] = [];
  let currentSection: { title: string; content: string[] } | null = null;
  
  for (const line of lines) {
    if (line.startsWith('## ')) {
      if (currentSection) {
        sections.push(currentSection);
      }
      currentSection = {
        title: line.replace('## ', '').trim(),
        content: []
      };
    } else if (line.startsWith('# ')) {
      continue;
    } else {
      if (currentSection) {
        currentSection.content.push(line);
      }
    }
  }
  if (currentSection) {
    sections.push(currentSection);
  }
  
  const tables = sections.filter(s => s.content.some(l => l.trim().startsWith('|')));
  const textSections = sections.filter(s => !s.content.some(l => l.trim().startsWith('|')));
  
  return (
    <div className="space-y-8 text-left">
      {/* Tables Section */}
      {tables.map((s, idx) => {
        const tableText = s.content.join('\n');
        const parsedTable = parseMarkdownTable(tableText);
        if (!parsedTable) return null;
        
        return (
          <div key={idx} className="bg-card border border-border rounded-3xl p-8 space-y-6 shadow-2xl">
            <h3 className="text-lg font-black text-white uppercase tracking-wider flex items-center space-x-2 border-b border-border/40 pb-4">
              <Activity className="w-5 h-5 text-accent animate-pulse" />
              <span>{s.title}</span>
            </h3>
            <div className="overflow-x-auto rounded-2xl border border-border/60">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-white/5 text-[10px] font-black uppercase text-gray-400 tracking-wider">
                    {parsedTable.headers.map((h, i) => (
                      <th key={i} className="py-5 px-6 font-bold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {parsedTable.rows.map((row, rIdx) => (
                    <tr key={rIdx} className="border-b border-border/20 last:border-0 hover:bg-white/2 transition-colors">
                      {row.map((cell, cIdx) => {
                        const isPnL = cell.includes('₹') || cell.includes('%');
                        const isPos = cell.includes('+');
                        const isNeg = cell.includes('-');
                        let textColor = "text-gray-300";
                        if (isPnL) {
                          if (isPos) textColor = "text-success font-black";
                          else if (isNeg) textColor = "text-danger font-black";
                        }
                        
                        return (
                          <td key={cIdx} className={`py-5 px-6 font-medium ${textColor}`}>
                            {cell}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
      
      {/* Critiques Section */}
      {textSections.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          {textSections.map((s, idx) => {
            const paragraphs = s.content
              .join('\n')
              .split('\n\n')
              .map(p => p.trim())
              .filter(Boolean);
            
            // Match icon based on heading title
            let IconComponent = Activity;
            if (s.title.toLowerCase().includes('critique')) IconComponent = Activity;
            else if (s.title.toLowerCase().includes('attribution')) IconComponent = TrendingUp;
            else if (s.title.toLowerCase().includes('selector')) IconComponent = Cpu;
            else if (s.title.toLowerCase().includes('directives') || s.title.toLowerCase().includes('recommendations')) IconComponent = Target;
              
            return (
              <div key={idx} className="bg-card border border-border rounded-3xl p-8 space-y-5 border-l-4 border-l-accent shadow-2xl flex flex-col">
                <h4 className="text-md font-black text-white uppercase tracking-wider flex items-center space-x-2 border-b border-border/40 pb-4">
                  <IconComponent className="w-5 h-5 text-accent" />
                  <span>{s.title}</span>
                </h4>
                <div className="space-y-4 flex-1 text-xs text-gray-400 leading-relaxed font-medium">
                  {paragraphs.map((p, pIdx) => {
                    const isList = p.includes('\n-') || p.startsWith('-') || p.includes('\n*') || p.startsWith('*') || p.startsWith('1.');
                    if (isList) {
                      const listItems = p.split(/\n[-*]|\n\d+\./).map(item => item.trim()).filter(Boolean);
                      return (
                        <ul key={pIdx} className="space-y-3 pl-1">
                          {listItems.map((item, itemIdx) => (
                            <li key={itemIdx} className="flex items-start space-x-3">
                              <span className="text-accent font-black select-none mt-0.5">•</span>
                              <span>{item}</span>
                            </li>
                          ))}
                        </ul>
                      );
                    }
                    return <p key={pIdx}>{p}</p>;
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AiAuditorView() {
  const [report, setReport] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const triggerAudit = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await runAiAudit()
      setReport(data.report)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Backend AI module offline. Ensure GROQ_API_KEY is set.'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  if (report) {
    return (
      <div className="space-y-8 animate-in fade-in-50 duration-500 w-full text-left">
        {/* Header section with title and clear button */}
        <div className="flex items-center justify-between border-b border-border pb-6">
          <div>
            <h2 className="text-3xl font-black text-white tracking-tighter uppercase mb-1">
              Post-Market Strategy Attribution Review
            </h2>
            <p className="text-xs text-gray-400">
              Empirical session performance matrix and dynamic strategy comparison playbooks.
            </p>
          </div>
          <button
            id="clear-report-btn"
            onClick={() => setReport(null)}
            className="px-6 py-2.5 bg-white/5 border border-border rounded-xl text-xs font-bold text-gray-300 uppercase hover:bg-white/10 hover:text-white transition-all shadow-md"
          >
            Clear Report
          </button>
        </div>

        {/* Structured report content */}
        <div className="space-y-8">
           {renderReportSections(report)}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-8 animate-in zoom-in-95 duration-500">
      <div className="bg-card border border-border rounded-3xl p-12 text-center max-w-2xl mx-auto shadow-2xl">
         <div className="w-24 h-24 bg-accent/10 rounded-full flex items-center justify-center mx-auto mb-8 neon-border">
            <Shield className="w-12 h-12 text-accent" />
         </div>
         <h2 className="text-4xl font-black text-white tracking-tighter uppercase mb-4">Post-Market Strategy Attribution Review</h2>
         <p className="text-gray-400 font-medium mb-10 leading-relaxed">
           Generate today's comparative performance attribution report. This dynamically backtests all 7
           options playbooks using NIFTY 5-minute database candles and compares them against your live trades.
         </p>
         
         {error && (
           <div className="mb-6 p-4 bg-danger/10 border border-danger/30 rounded-xl text-left">
             <p className="text-xs text-danger font-bold uppercase mb-1">Review Failed</p>
             <p className="text-xs text-gray-400">{error}</p>
           </div>
         )}

         <button 
           id="execute-audit-btn"
           onClick={triggerAudit}
           disabled={loading}
           className="w-full py-4 bg-accent text-bg font-black rounded-xl hover:bg-accent/90 transition-all flex items-center justify-center space-x-3 disabled:opacity-50"
         >
           {loading ? <RefreshCw className="w-5 h-5 animate-spin" /> : <Play className="w-5 h-5 fill-current" />}
           <span>{loading ? "RUNNING ATTRIBUTION REVIEW..." : "RUN SESSION REVIEW"}</span>
         </button>
      </div>
    </div>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
    </QueryClientProvider>
  )
}

export default App
