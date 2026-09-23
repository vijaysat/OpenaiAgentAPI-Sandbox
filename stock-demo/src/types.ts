export interface Stock {
  readonly symbol: string;
  readonly name: string;
  readonly exchange: string;
}

export interface Quote extends Stock {
  readonly currency: string;
  readonly price: number;
  readonly previousClose: number | null;
  readonly change: number | null;
  readonly changePercent: number | null;
  readonly dayHigh: number | null;
  readonly dayLow: number | null;
  readonly asOf: string;
  readonly fetchedAt: string;
  readonly source: string;
}

export interface StockOption extends Stock {
  readonly onSelect: () => void;
}

export interface AgentRun {
  readonly agentId: string | null;
  readonly sessionId: string | null;
  readonly turnId: string | null;
  readonly model: string;
  readonly status: string;
  readonly output: string;
  readonly tools: readonly { readonly name: string; readonly callId: string; readonly success: boolean }[];
  readonly events: readonly string[];
  readonly execution?: string;
  readonly sandboxId?: string | null;
}

export interface QuotePresentation {
  readonly value: Quote;
  readonly price: string;
  readonly change: string;
  readonly falling: boolean;
  readonly previousClose: string;
  readonly dayRange: string;
  readonly asOf: string;
  readonly fetchedAt: string;
}
