export type TargetWeights = Record<string, number>;
const SYMBOL = /^[A-Z][A-Z0-9.-]{0,14}$/;

export function modelWeights(heldSymbols: string[], targets: TargetWeights): TargetWeights {
  return { ...Object.fromEntries(heldSymbols.map(symbol => [symbol, 0])), CASH: 0, ...targets };
}

export function addTargetSymbol(weights: TargetWeights, rawSymbol: string): TargetWeights {
  const symbol = rawSymbol.trim().toUpperCase();
  if (!SYMBOL.test(symbol)) throw new Error('Enter a ticker of up to 15 characters, starting with a letter. Letters, numbers, dots and hyphens are supported.');
  if (Object.hasOwn(weights, symbol)) throw new Error(`${symbol} is already listed. Edit its target percentage below.`);
  if (Object.keys(weights).length >= 201) throw new Error('The symbol limit has been reached. Remove an unused target before adding another.');
  return { ...weights, [symbol]: 0 };
}

export function removeTargetSymbol(weights: TargetWeights, symbol: string, heldSymbols: string[]): TargetWeights {
  if (symbol === 'CASH') return weights;
  const next = { ...weights };
  if (heldSymbols.includes(symbol)) next[symbol] = 0;
  else delete next[symbol];
  return next;
}

export function targetValidation(weights: TargetWeights): string | null {
  const entries = Object.entries(weights);
  if (!entries.length || entries.length > 201 || entries.some(([symbol]) => !SYMBOL.test(symbol))) return 'Use valid ticker symbols for your targets.';
  if (entries.some(([, weight]) => !Number.isFinite(weight) || weight < 0 || weight > 100)) return 'Each target must be between 0 and 100%.';
  if (entries.filter(([symbol, weight]) => symbol !== 'CASH' && weight > 0).length > 100) return 'At most 100 stocks or ETFs can have a target above 0%.';
  if (Math.abs(entries.reduce((total, [, weight]) => total + weight, 0) - 100) >= 0.00001) return 'Target weights must total 100% before analyzing.';
  return null;
}

export function targetsChanged(weights: TargetWeights, rows: {symbol: string; targetWeight: number}[]): boolean {
  const saved = Object.fromEntries(rows.map(row => [row.symbol, row.targetWeight]));
  return [...new Set([...Object.keys(weights), ...Object.keys(saved)])].some(symbol =>
    !Number.isFinite(weights[symbol] ?? 0) || Math.abs((weights[symbol] ?? 0) - (saved[symbol] ?? 0)) > 0.00001);
}
