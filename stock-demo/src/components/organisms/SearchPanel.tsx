import React from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { copy } from '../../data/mockData';
import { theme } from '../../theme';
import { ActionButton } from '../atoms/ActionButton';

export interface SearchPanelProps {
  readonly query: string;
  readonly onChange: (value: string) => void;
  readonly onSubmit: () => void;
  readonly busy: boolean;
  readonly compact: boolean;
  readonly examples: readonly { readonly name: string; readonly onPress: () => void }[];
}

export function SearchPanel({ query, onChange, onSubmit, busy, compact, examples }: SearchPanelProps) {
  return <View style={styles.panel}>
    <Text style={styles.label}>{copy.searchLabel}</Text>
    <View style={[styles.searchRow, compact && styles.stacked]}>
      <TextInput value={query} onChangeText={onChange} onSubmitEditing={onSubmit}
        placeholder={copy.placeholder} placeholderTextColor={theme.colors.muted} maxLength={80}
        autoCorrect={false} autoCapitalize="none" returnKeyType="search" editable={!busy}
        accessibilityLabel={copy.searchLabel} accessibilityRole="search" role="searchbox" style={styles.input} />
      <ActionButton label={busy ? copy.searching : copy.search} onPress={onSubmit} disabled={busy} />
    </View>
    <View style={styles.examples}>
      <Text style={styles.hint}>{copy.suggestions}</Text>
      {examples.map(example => <Pressable key={example.name} onPress={example.onPress} disabled={busy} accessibilityState={{ disabled: busy }}
        accessibilityRole="button" accessibilityLabel={copy.exampleLabel(example.name)} style={styles.chip}>
        <Text style={styles.chipText}>{example.name}</Text>
      </Pressable>)}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  panel: { backgroundColor: theme.colors.paper, padding: theme.space.xl, borderRadius: theme.radius.lg,
    borderWidth: theme.layout.border, borderColor: theme.colors.line, gap: theme.space.md, marginBottom: theme.space.xl },
  label: { color: theme.colors.ink, fontFamily: theme.font.body, fontSize: theme.size.small, fontWeight: theme.weight.bold },
  searchRow: { flexDirection: 'row', gap: theme.space.md }, stacked: { flexDirection: 'column' },
  input: { flex: 1, minHeight: theme.layout.touch, borderColor: theme.colors.line, borderWidth: theme.layout.border,
    borderRadius: theme.radius.md, paddingHorizontal: theme.space.lg, paddingVertical: theme.space.md,
    color: theme.colors.ink, fontFamily: theme.font.body, fontSize: theme.size.body, backgroundColor: theme.colors.canvas },
  examples: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: theme.space.sm },
  hint: { color: theme.colors.muted, fontFamily: theme.font.mono, fontSize: theme.size.tiny, marginRight: theme.space.xs },
  chip: { minHeight: theme.layout.touch, justifyContent: 'center', paddingHorizontal: theme.space.md, borderRadius: theme.radius.pill,
    backgroundColor: theme.colors.canvas },
  chipText: { color: theme.colors.green, fontFamily: theme.font.body, fontSize: theme.size.small },
});
