import React from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';
import { theme } from '../../theme';

export interface ActionButtonProps {
  readonly label: string;
  readonly onPress: () => void;
  readonly disabled?: boolean;
  readonly secondary?: boolean;
}

export function ActionButton({ label, onPress, disabled = false, secondary = false }: ActionButtonProps) {
  return <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button"
    accessibilityLabel={label} accessibilityState={{ disabled }}
    style={({ pressed }) => [styles.button, secondary && styles.secondary, pressed && styles.pressed, disabled && styles.disabled]}>
    <Text style={[styles.label, secondary && styles.secondaryLabel]}>{label}</Text>
  </Pressable>;
}

const styles = StyleSheet.create({
  button: { minHeight: theme.layout.touch, justifyContent: 'center', alignItems: 'center', paddingHorizontal: theme.space.xl,
    borderRadius: theme.radius.md, backgroundColor: theme.colors.green },
  label: { color: theme.colors.onGreen, fontFamily: theme.font.body, fontSize: theme.size.body, fontWeight: theme.weight.bold },
  secondary: { backgroundColor: theme.colors.pale },
  secondaryLabel: { color: theme.colors.green },
  pressed: { opacity: 0.75 }, disabled: { opacity: 0.55 },
});
