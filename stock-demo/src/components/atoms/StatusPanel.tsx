import React from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { theme } from '../../theme';

export interface StatusPanelProps {
  readonly title: string;
  readonly message?: string;
  readonly busy?: boolean;
  readonly error?: boolean;
}

export function StatusPanel({ title, message, busy, error }: StatusPanelProps) {
  return <View style={[styles.panel, error && styles.error]} accessibilityLiveRegion="polite">
    {busy && <ActivityIndicator color={theme.colors.green} accessibilityLabel={title} />}
    <Text accessibilityRole={error ? 'alert' : 'text'} style={[styles.title, error && styles.errorText]}>{title}</Text>
    {message && <Text style={styles.message}>{message}</Text>}
  </View>;
}

const styles = StyleSheet.create({
  panel: { padding: theme.space.xl, borderRadius: theme.radius.md, backgroundColor: theme.colors.pale, gap: theme.space.sm },
  title: { color: theme.colors.ink, fontSize: theme.size.body, fontFamily: theme.font.body, fontWeight: theme.weight.medium },
  message: { color: theme.colors.muted, fontSize: theme.size.small, lineHeight: theme.line.body, fontFamily: theme.font.body },
  error: { backgroundColor: theme.colors.errorSurface }, errorText: { color: theme.colors.error },
});
