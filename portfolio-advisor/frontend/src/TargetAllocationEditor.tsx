import { useRef, useState } from 'react';
import { ChevronDown, Plus, X } from 'lucide-react';
import { addTargetSymbol, removeTargetSymbol, targetValidation, type TargetWeights } from './targetAllocation';

export function TargetAllocationEditor({ weights, heldSymbols, onChange }: {
  weights: TargetWeights; heldSymbols: string[]; onChange: (weights: TargetWeights) => void;
}) {
  const [symbol, setSymbol] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const symbolInput = useRef<HTMLInputElement>(null);
  const weightInputs = useRef<Record<string, HTMLInputElement | null>>({});
  const total = Object.values(weights).reduce((sum, weight) => sum + weight, 0);
  const invalid = targetValidation(weights);

  function add() {
    try {
      const next = addTargetSymbol(weights, symbol);
      const added = symbol.trim().toUpperCase();
      onChange(next);
      setSymbol(''); setError('');
      setMessage(`${added} added at 0%. Set its target and adjust other weights to total 100%.`);
      requestAnimationFrame(() => { weightInputs.current[added]?.focus(); weightInputs.current[added]?.select(); });
    } catch (e) { setError((e as Error).message); setMessage(''); }
  }

  function remove(ticker: string) {
    onChange(removeTargetSymbol(weights, ticker, heldSymbols));
    setError('');
    setMessage(heldSymbols.includes(ticker) ? `${ticker} target set to 0%. Your actual holding is unchanged.` : `${ticker} removed from the target allocation.`);
    symbolInput.current?.focus();
  }

  return <details className="target-editor" open>
    <summary>Edit target allocation <ChevronDown size={15}/></summary>
    <p className="target-help">Add a stock or ETF to explore buying it. Your actual holdings stay unchanged.</p>
    <form className="target-add" onSubmit={event => { event.preventDefault(); add(); }}>
      <label htmlFor="target-symbol">New stock or ETF symbol</label>
      <div><input id="target-symbol" ref={symbolInput} value={symbol} placeholder="e.g. AMZN" maxLength={15} autoComplete="off" autoCapitalize="characters" spellCheck={false}
        aria-invalid={!!error} aria-describedby={error ? 'target-symbol-error target-symbol-help' : 'target-symbol-help'}
        onChange={event => { setSymbol(event.target.value); setError(''); }}/>
        <button className="button" type="submit" disabled={!symbol.trim()}><Plus size={15}/> Add</button></div>
      <small id="target-symbol-help">Use US-listed stocks or ETFs. Availability and USD prices are checked during analysis.</small>
      {error && <p id="target-symbol-error" className="target-error" role="alert">{error}</p>}
    </form>
    <div className="target-weights">{Object.entries(weights).map(([ticker, weight]) => {
      const held = heldSymbols.includes(ticker);
      return <div className="target-row" key={ticker}>
        <label htmlFor={`target-weight-${ticker}`}><strong>{ticker}</strong><small>{ticker === 'CASH' ? 'USD cash' : held ? 'Current holding' : 'Target only'}</small></label>
        <div className="target-value"><input id={`target-weight-${ticker}`} ref={node => { weightInputs.current[ticker] = node; }} aria-label={`${ticker} target percent`}
          type="number" min="0" max="100" step="0.1" inputMode="decimal" aria-invalid={!Number.isFinite(weight) || weight < 0 || weight > 100}
          value={Number.isNaN(weight) ? '' : weight} onChange={event => { onChange({ ...weights, [ticker]: event.target.value === '' ? NaN : Number(event.target.value) }); setMessage(''); }}/><span>%</span></div>
        {ticker !== 'CASH' ? <button type="button" className="target-remove" disabled={held && weight === 0}
          aria-label={held ? `Set ${ticker} target to zero` : `Remove ${ticker} from target allocation`}
          title={held ? 'Set target to 0%; keep actual holding' : 'Remove target symbol'} onClick={() => remove(ticker)}><X size={15}/></button> : <span/>}
      </div>;
    })}</div>
    <div className={invalid ? 'weight-total invalid' : 'weight-total'}>Total <strong>{Number.isFinite(total) ? total.toFixed(1) : '—'}%</strong></div>
    {invalid && <p className="target-error" role="status">{invalid}</p>}
    <p className="target-feedback" role="status">{message}</p>
    <p className="target-help">A 0% target explores selling that holding completely. Selecting a preset replaces these targets. Analyze again to update your saved proposal.</p>
  </details>;
}
