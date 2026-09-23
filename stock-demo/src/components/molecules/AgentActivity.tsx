import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { AgentRun } from '../../types';
import { copy } from '../../data/mockData';
import { theme } from '../../theme';
import { StatusPanel } from '../atoms/StatusPanel';

export interface AgentActivityProps {
  readonly run: AgentRun | null;
  readonly busy: boolean;
  readonly sessionId: string | null;
}

export function AgentActivity({ run, busy, sessionId }: AgentActivityProps) {
  return <View style={styles.card}>
    <Text accessibilityRole="header" style={styles.title}>{copy.agentTitle}</Text>
    {sessionId && <><Text style={styles.note}>{copy.configuredSession}</Text><Text selectable style={styles.identifier}>{sessionId}</Text></>}
    {busy ? <StatusPanel busy title={copy.agentWaiting} /> : !run ? <Text style={styles.note}>{copy.agentExplanation}</Text> : <>
      <Text style={styles.status}>{run.status === 'completed' ? copy.agentComplete : copy.agentIncomplete}</Text>
      <Text style={styles.note}>{copy.agentModel}</Text><Text selectable style={styles.value}>{run.model}</Text>
      {run.execution && <><Text style={styles.note}>{copy.execution}</Text><Text style={styles.value}>{run.execution}</Text></>}
      {run.tools.map(tool => <View key={tool.callId} style={styles.tool}>
        <Text style={styles.value}>{tool.name}</Text>
        <Text style={styles.note}>{tool.success ? copy.toolSuccess : copy.toolFailed}</Text>
      </View>)}
      {run.output ? <><Text style={styles.note}>{copy.agentResponse}</Text><Text selectable style={styles.response}>{run.output}</Text></> : null}
      {run.sessionId ? <><Text style={styles.note}>{copy.agentSession}</Text><Text selectable style={styles.identifier}>{run.sessionId}</Text></> : null}
      {run.turnId ? <><Text style={styles.note}>{copy.agentTurn}</Text><Text selectable style={styles.identifier}>{run.turnId}</Text></> : null}
    </>}
  </View>;
}

const styles = StyleSheet.create({
  card: { padding: theme.space.xl, backgroundColor: theme.colors.paper, borderRadius: theme.radius.lg,
    borderWidth: theme.layout.border, borderColor: theme.colors.line, gap: theme.space.sm, marginBottom: theme.space.xl },
  title: { color: theme.colors.ink, fontFamily: theme.font.body, fontSize: theme.size.title, fontWeight: theme.weight.medium },
  note: { color: theme.colors.muted, fontFamily: theme.font.body, fontSize: theme.size.small, lineHeight: theme.line.small },
  status: { color: theme.colors.green, fontFamily: theme.font.body, fontSize: theme.size.body, fontWeight: theme.weight.bold },
  value: { color: theme.colors.ink, fontFamily: theme.font.mono, fontSize: theme.size.small },
  response: { color: theme.colors.ink, fontFamily: theme.font.body, fontSize: theme.size.body, lineHeight: theme.line.body },
  identifier: { color: theme.colors.ink, fontFamily: theme.font.mono, fontSize: theme.size.tiny, lineHeight: theme.line.small },
  tool: { backgroundColor: theme.colors.pale, borderRadius: theme.radius.sm, padding: theme.space.md, gap: theme.space.xs },
});
