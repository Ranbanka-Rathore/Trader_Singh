import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000/api/v1';

export const api = axios.create({
  baseURL: API_BASE_URL,
});

export interface SignalAudit {
  id: number;
  timestamp: string;
  ticker: string;
  pa_status: string;
  pcr: number;
  gex_mn: number;
  ml_score: number;
  committee_verdict: string;
  committee_reasoning?: string;
  backtester_rule_match?: string;
}

export interface OpenPosition {
  id: number;
  ticker: string;
  strategy_type: string;
  spot_price: number;
  net_credit_per_share: number;
  lots_sized: number;
  entry_date: string;
  net_delta: number;
  net_gamma: number;
  net_theta: number;
}

export interface PortfolioStats {
  portfolio_var: number;
  daily_pnl: number;
  total_exposure: number;
  est_margin_deployed?: number;
  net_delta: number;
  net_theta: number;
  net_gamma: number;
  net_vega: number;
  positions_count: number;
  is_within_limits: boolean;
  timestamp: string;
  total_gex?: number;
  kill_reason?: string | null;
}

export interface AutopilotStatus {
  status: 'ACTIVE' | 'IDLE' | 'OFFLINE' | 'PAUSED' | 'WAITING_FOR_MARKET';
  last_run: string | null;
  active_nodes?: number;
  source?: string;
}

export interface Trade {
  id: number;
  ticker: string;
  strategy_type: string;
  entry_date: string;
  exit_date: string;
  entry_spot_price: number;
  exit_price: number;
  net_credit_per_share: number;
  lots_sized: number;
  realized_pnl: number;
  exit_reason: string;
}

export const getSignals = async (limit = 50): Promise<SignalAudit[]> => {
  const response = await api.get<SignalAudit[]>(`/signals?limit=${limit}`);
  return response.data;
};

export const getTrades = async (limit = 50): Promise<Trade[]> => {
  const response = await api.get<Trade[]>(`/trades?limit=${limit}`);
  return response.data;
};

export const getAutopilotStatus = async (): Promise<AutopilotStatus> => {
  const response = await api.get<AutopilotStatus>('/autopilot/status');
  return response.data;
};

export const startAutopilot = async (): Promise<void> => {
  await api.post('/autopilot/start');
};

export const stopAutopilot = async (): Promise<void> => {
  await api.post('/autopilot/stop');
};

export const getPositions = async (): Promise<OpenPosition[]> => {
  const response = await api.get<OpenPosition[]>('/positions');
  return response.data;
};

export const getStats = async (): Promise<PortfolioStats> => {
  const response = await api.get<PortfolioStats>('/stats');
  return response.data;
};

export const runAiAudit = async (): Promise<{ report: string }> => {
  const response = await api.get<{ report: string }>('/ai/audit');
  return response.data;
};

export const getDevMode = async (): Promise<boolean> => {
  const response = await api.get<{ dev_mode: boolean }>('/autopilot/dev_mode');
  return response.data.dev_mode;
};

export const toggleDevMode = async (status: boolean): Promise<void> => {
  await api.post(`/autopilot/dev_mode?status=${status}`);
};

export const getStrategyMode = async (): Promise<string> => {
  const response = await api.get<{ strategy_mode: string }>('/autopilot/strategy-mode');
  return response.data.strategy_mode;
};

export const setStrategyMode = async (mode: string): Promise<void> => {
  await api.post(`/autopilot/strategy-mode?mode=${mode}`);
};

export const getFirefighting = async (): Promise<boolean> => {
  const response = await api.get<{ firefighting: boolean }>('/autopilot/firefighting');
  return response.data.firefighting;
};

export const toggleFirefighting = async (status: boolean): Promise<void> => {
  await api.post(`/autopilot/firefighting?status=${status}`);
};


export interface TickData {
  t: string;
  p: number;
  v: number;
  source?: 'DHAN_LIVE' | 'SIMULATED';
}

export const getRecentTicks = async (ticker: string): Promise<TickData[]> => {
  const response = await api.get<TickData[]>(`/ticks/${ticker}`);
  return response.data;
};

export const getMarketSnapshot = async (ticker: string): Promise<any> => {
  const response = await api.get<any>(`/snapshot/${ticker}`);
  return response.data;
};

export const getStructuralLevels = async (ticker: string): Promise<any> => {
  const response = await api.get<any>(`/levels/${ticker}`);
  return response.data;
};

export const getStrategySelectionMode = async (): Promise<string> => {
  const response = await api.get<{ mode: string }>('/autopilot/strategy-selection');
  return response.data.mode;
};

export const setStrategySelectionMode = async (mode: string): Promise<void> => {
  await api.post(`/autopilot/strategy-selection?mode=${mode}`);
};

export const getLowMarginMode = async (): Promise<boolean> => {
  const response = await api.get<{ low_margin: boolean }>('/autopilot/low-margin');
  return response.data.low_margin;
};

export const toggleLowMarginMode = async (status: boolean): Promise<void> => {
  await api.post(`/autopilot/low-margin?status=${status}`);
};
