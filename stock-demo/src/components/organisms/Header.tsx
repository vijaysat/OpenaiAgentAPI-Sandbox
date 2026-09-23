import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { copy } from '../../data/mockData';
import { theme } from '../../theme';

export interface HeaderProps { readonly compact: boolean }

export function Header({ compact }: HeaderProps) {
  return <View>
    <View style={styles.nav}>
      <View style={styles.brandGroup}>
        <View style={styles.mark}><Text style={styles.markText}>{copy.brandMark}</Text></View>
        <Text style={styles.brand}>{copy.brand}</Text>
      </View>
      <Text style={styles.edition}>{copy.edition}</Text>
    </View>
    <View style={styles.hero}>
      <Text style={styles.badge}>{copy.badge}</Text>
      <Text accessibilityRole="header" style={[styles.title, compact && styles.compactTitle]}>{copy.headline}</Text>
      <Text style={styles.description}>{copy.description}</Text>
    </View>
  </View>;
}

const styles = StyleSheet.create({
  nav: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: theme.space.md,
    paddingVertical: theme.space.xl, borderBottomWidth: theme.layout.border, borderBottomColor: theme.colors.line },
  brandGroup: { flexDirection: 'row', alignItems: 'center', gap: theme.space.sm },
  mark: { width: theme.layout.mark, height: theme.layout.mark, borderRadius: theme.radius.sm,
    backgroundColor: theme.colors.green, justifyContent: 'center', alignItems: 'center' },
  markText: { color: theme.colors.onGreen, fontSize: theme.size.title },
  brand: { color: theme.colors.green, fontFamily: theme.font.display, fontSize: theme.size.heading },
  edition: { color: theme.colors.muted, fontFamily: theme.font.mono, fontSize: theme.size.tiny },
  hero: { paddingVertical: theme.space.xxl, gap: theme.space.lg },
  badge: { color: theme.colors.green, fontFamily: theme.font.body, fontSize: theme.size.small, fontWeight: theme.weight.medium },
  title: { color: theme.colors.ink, fontFamily: theme.font.display, fontSize: theme.size.hero, lineHeight: theme.line.hero },
  compactTitle: { fontSize: theme.size.heading, lineHeight: theme.line.heading },
  description: { color: theme.colors.muted, fontFamily: theme.font.body, fontSize: theme.size.body, lineHeight: theme.line.body },
});
