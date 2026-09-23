import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Platform } from 'react-native';
import { copy } from '../data/mockData';
import type { AgentRun, Quote, QuotePresentation, Stock, StockOption } from '../types';

const baseUrl = (process.env.EXPO_PUBLIC_API_URL ||
  (Platform.OS === 'android' ? 'http://10.0.2.2:8766' : 'http://localhost:8766')).replace(/\/$/, '');

class AgentRequestError extends Error {
  constructor(message: string, readonly agent?: AgentRun) { super(message); }
}

// A correlation/idempotency key, not a credential. Works in native and web runtimes.
const requestId = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, character => {
  const digit = Math.floor(Math.random() * 16);
  return (character === 'x' ? digit : (digit & 3) | 8).toString(16);
});

async function request<T>(path: string, body: Record<string, string>, controller: AbortController): Promise<T> {
  let timedOut = false;
  const id = requestId();
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 210000);
  try {
    const response = await fetch(`${baseUrl}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, requestId: id }), signal: controller.signal });
    const data = await response.json();
    if (!response.ok) throw new AgentRequestError(data.error || copy.badResponse, data.agent);
    return data as T;
  } catch (error) {
    if (timedOut) throw new Error(`${copy.timeout} ${id}`);
    if (error instanceof TypeError) throw new Error(copy.connection);
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

const decimal = (value: number | null) => value === null ? copy.quotePlaceholder :
  value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
const date = (value: string) => new Date(value).toLocaleString(undefined, {
  year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
});

export function useStockLookup() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<readonly Stock[]>([]);
  const [selected, setSelected] = useState<Stock | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [searching, setSearching] = useState(false);
  const [loadingQuote, setLoadingQuote] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [agentRun, setAgentRun] = useState<AgentRun | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const searchRequest = useRef<AbortController | null>(null);
  const quoteRequest = useRef<AbortController | null>(null);

  useEffect(() => () => { searchRequest.current?.abort(); quoteRequest.current?.abort(); }, []);

  useEffect(() => {
    const controller = new AbortController();
    void fetch(`${baseUrl}/api/health`, { signal: controller.signal }).then(response => response.json())
      .then(data => { if (!controller.signal.aborted && typeof data.sessionId === 'string') setSessionId(data.sessionId); })
      .catch(() => { /* A lookup provides the actionable connection error. */ });
    return () => controller.abort();
  }, []);

  const reset = useCallback(() => {
    searchRequest.current?.abort(); quoteRequest.current?.abort();
    searchRequest.current = null; quoteRequest.current = null;
    setSearching(false); setLoadingQuote(false); setResults([]); setSelected(null);
    setQuote(null); setError(null); setQuoteError(null); setSearched(false); setAgentRun(null);
  }, []);

  const changeQuery = useCallback((text: string) => { reset(); setQuery(text); }, [reset]);

  const search = useCallback(async (text: string) => {
    reset(); setQuery(text);
    if (text.trim().length < 2) { setError(copy.validation); return; }
    const controller = new AbortController();
    searchRequest.current = controller; setSearching(true);
    try {
      const data = await request<{ results: Stock[]; agent: AgentRun }>('/api/search', { query: text.trim() }, controller);
      if (searchRequest.current !== controller) return;
      if (!Array.isArray(data.results)) throw new Error(copy.badResponse);
      setResults(data.results); setSearched(true); setAgentRun(data.agent);
    } catch (err) {
      if (searchRequest.current === controller) {
        setError(err instanceof Error ? err.message : copy.connection);
        if (err instanceof AgentRequestError && err.agent) setAgentRun(err.agent);
      }
    } finally {
      if (searchRequest.current === controller) setSearching(false);
    }
  }, [reset]);

  const select = useCallback(async (stock: Stock) => {
    quoteRequest.current?.abort();
    const controller = new AbortController(); quoteRequest.current = controller;
    setSelected(stock); setQuote(null); setQuoteError(null); setLoadingQuote(true); setAgentRun(null);
    try {
      const data = await request<{ quote: Quote; agent: AgentRun }>('/api/quote', { symbol: stock.symbol }, controller);
      const value = data.quote;
      if (quoteRequest.current !== controller) return;
      if (!Number.isFinite(value.price) || !value.currency || !value.asOf || value.symbol !== stock.symbol) throw new Error(copy.badResponse);
      setQuote(value); setAgentRun(data.agent);
    } catch (err) {
      if (quoteRequest.current === controller) {
        setQuoteError(err instanceof Error ? err.message : copy.connection);
        if (err instanceof AgentRequestError && err.agent) setAgentRun(err.agent);
      }
    } finally {
      if (quoteRequest.current === controller) setLoadingQuote(false);
    }
  }, []);

  const submit = useCallback(() => { void search(query); }, [query, search]);
  const refresh = useCallback(() => { if (selected) void select(selected); }, [selected, select]);
  const examples = useMemo(() => copy.examples.map(name => ({ name, onPress: () => { void search(name); } })), [search]);
  const options: readonly StockOption[] = useMemo(() => results.map(stock => ({ ...stock, onSelect: () => { void select(stock); } })), [results, select]);
  const presentation: QuotePresentation | null = useMemo(() => quote ? {
    value: quote, price: decimal(quote.price), falling: (quote.change ?? 0) < 0,
    change: quote.change === null || quote.changePercent === null ? copy.quotePlaceholder :
      `${quote.change >= 0 ? '+' : ''}${decimal(quote.change)} (${quote.changePercent >= 0 ? '+' : ''}${quote.changePercent.toFixed(2)}%)`,
    previousClose: decimal(quote.previousClose),
    dayRange: `${decimal(quote.dayLow)} – ${decimal(quote.dayHigh)}`,
    asOf: date(quote.asOf), fetchedAt: date(quote.fetchedAt),
  } : null, [quote]);

  return { query, changeQuery, submit, examples, options, selected, presentation, refresh,
    searching, loadingQuote, searched, error, quoteError, agentRun, sessionId };
}
