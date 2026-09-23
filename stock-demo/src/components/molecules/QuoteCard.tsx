import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { copy } from '../../data/mockData';
import { theme } from '../../theme';
import type { QuotePresentation, Stock } from '../../types';
import { ActionButton } from '../atoms/ActionButton';
import { StatusPanel } from '../atoms/StatusPanel';

export interface QuoteCardProps {
  readonly selected: Stock;
  readonly quote: QuotePresentation | null;
  readonly busy: boolean;
  readonly error: string | null;
  readonly onRefresh: () => void;
}

export function QuoteCard({ selected, quote, busy, error, onRefresh }: QuoteCardProps) {
  return <View style={styles.outer}>
    <View style={styles.card}>
      <View style={styles.top}>
        <Text style={styles.eyebrow}>{copy.selected}</Text>
        <Text style={styles.symbol}>{selected.symbol}</Text>
      </View>
      <Text accessibilityRole="header" style={styles.name}>{selected.name}</Text>
      <Text style={styles.exchange}>{selected.exchange}</Text>
      {busy && <StatusPanel busy title={copy.loading} />}
      {error && <StatusPanel error title={error} />}
      {quote && <>
        <View style={styles.priceGroup}>
          <Text style={styles.label}>{copy.latest}</Text>
          <View style={styles.priceRow}>
            <Text selectable style={styles.price}>{quote.price}</Text>
            <Text style={styles.currency}>{quote.value.currency}</Text>
          </View>
          <Text style={[styles.change, quote.falling && styles.falling]}>{quote.change}</Text>
          <Text style={styles.label}>{copy.changeLabel}</Text>
        </View>
        <View style={styles.stats}>
          <View style={styles.stat}><Text style={styles.label}>{copy.previousClose}</Text><Text style={styles.statValue}>{quote.previousClose}</Text></View>
          <View style={styles.stat}><Text style={styles.label}>{copy.dayRange}</Text><Text style={styles.statValue}>{quote.dayRange}</Text></View>
        </View>
        <View style={styles.times}>
          <Text style={styles.label}>{copy.asOf}</Text><Text style={styles.timeValue}>{quote.asOf}</Text>
          <Text style={styles.label}>{copy.fetchedAt}</Text><Text style={styles.timeValue}>{quote.fetchedAt}</Text>
          <Text style={styles.label}>{copy.source}</Text><Text style={styles.timeValue}>{quote.value.source}</Text>
        </View>
      </>}
      <ActionButton label={busy ? copy.refreshing : copy.refresh} onPress={onRefresh} disabled={busy} secondary />
    </View>
    <Text style={styles.notice}>{copy.priceNote}</Text>
  </View>;
}

const styles = StyleSheet.create({
  outer: { marginVertical: theme.space.xl, gap: theme.space.md },
  card: { backgroundColor: theme.colors.green, borderRadius: theme.radius.lg, padding: theme.space.xl, gap: theme.space.md },
  top: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: theme.space.lg },
  eyebrow: { fontFamily: theme.font.body, color: theme.colors.onGreenMuted, fontSize: theme.size.small },
  symbol: { fontFamily: theme.font.mono, color: theme.colors.onGreen, fontSize: theme.size.small, fontWeight: theme.weight.bold },
  name: { fontFamily: theme.font.body, color: theme.colors.onGreen, fontSize: theme.size.title, fontWeight: theme.weight.bold },
  exchange: { fontFamily: theme.font.body, color: theme.colors.onGreenMuted, fontSize: theme.size.small },
  priceGroup: { marginTop: theme.space.lg, gap: theme.space.xs },
  priceRow: { flexDirection: 'row', alignItems: 'baseline', flexWrap: 'wrap', gap: theme.space.md },
  price: { color: theme.colors.onGreen, fontFamily: theme.font.body, fontSize: theme.size.price, lineHeight: theme.line.price, fontWeight: theme.weight.medium },
  currency: { color: theme.colors.onGreenMuted, fontFamily: theme.font.mono, fontSize: theme.size.body },
  change: { color: theme.colors.positive, fontFamily: theme.font.body, fontSize: theme.size.body, fontWeight: theme.weight.medium },
  falling: { color: theme.colors.negative },
  label: { color: theme.colors.onGreenMuted, fontFamily: theme.font.body, fontSize: theme.size.small, lineHeight: theme.line.small },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: theme.space.xxl, paddingVertical: theme.space.xl,
    borderBottomWidth: theme.layout.border, borderBottomColor: theme.colors.onGreenMuted },
  stat: { gap: theme.space.sm },
  statValue: { color: theme.colors.onGreen, fontSize: theme.size.body, fontFamily: theme.font.mono },
  times: { gap: theme.space.xs, marginBottom: theme.space.md },
  timeValue: { color: theme.colors.onGreen, fontFamily: theme.font.body, fontSize: theme.size.small, lineHeight: theme.line.body },
  notice: { color: theme.colors.muted, fontFamily: theme.font.body, fontSize: theme.size.small, lineHeight: theme.line.small },
});
