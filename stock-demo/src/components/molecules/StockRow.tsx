import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { copy } from '../../data/mockData';
import { theme } from '../../theme';
import type { StockOption } from '../../types';

export interface StockRowProps {
  readonly stock: StockOption;
  readonly selected: boolean;
  readonly disabled: boolean;
}

export function StockRow({ stock, selected, disabled }: StockRowProps) {
  return <Pressable onPress={stock.onSelect} disabled={disabled} accessibilityRole="button"
    accessibilityLabel={copy.selectLabel(stock.name, stock.symbol, stock.exchange)} accessibilityState={{ selected, disabled }}
    style={({ pressed }) => [styles.row, selected && styles.selected, pressed && styles.pressed]}>
    <View style={styles.symbolBox}><Text style={styles.symbol}>{stock.symbol}</Text></View>
    <View style={styles.details}>
      <Text style={styles.name}>{stock.name}</Text>
      <Text style={styles.exchange}>{stock.exchange}</Text>
    </View>
    <Text style={styles.arrow} accessibilityElementsHidden importantForAccessibility="no">{copy.brandMark}</Text>
  </Pressable>;
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: theme.space.lg, paddingVertical: theme.space.xl,
    paddingHorizontal: theme.space.lg, borderBottomWidth: theme.layout.border, borderBottomColor: theme.colors.line },
  selected: { backgroundColor: theme.colors.pale, borderRadius: theme.radius.md },
  pressed: { opacity: 0.7 },
  symbolBox: { backgroundColor: theme.colors.paper, borderRadius: theme.radius.sm, padding: theme.space.md,
    borderWidth: theme.layout.border, borderColor: theme.colors.line },
  symbol: { fontFamily: theme.font.mono, fontSize: theme.size.small, fontWeight: theme.weight.bold, color: theme.colors.green },
  details: { flex: 1, gap: theme.space.xs },
  name: { fontFamily: theme.font.body, fontSize: theme.size.body, fontWeight: theme.weight.medium, color: theme.colors.ink },
  exchange: { fontFamily: theme.font.body, fontSize: theme.size.small, color: theme.colors.muted },
  arrow: { fontSize: theme.size.title, color: theme.colors.green },
});
