import React from 'react';
import { FlatList, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Header } from '../components/organisms/Header';
import { SearchPanel } from '../components/organisms/SearchPanel';
import { QuoteCard } from '../components/molecules/QuoteCard';
import { AgentActivity } from '../components/molecules/AgentActivity';
import { StockRow } from '../components/molecules/StockRow';
import { StatusPanel } from '../components/atoms/StatusPanel';
import { useStockLookup } from '../hooks/useStockLookup';
import { copy } from '../data/mockData';
import { theme } from '../theme';
import type { StockOption } from '../types';

export interface StockScreenProps { readonly testID?: string }

const keyExtractor = (stock: StockOption) => stock.symbol;

export function StockScreen({ testID }: StockScreenProps) {
  const lookup = useStockLookup();
  const { width } = useWindowDimensions();
  const compact = width < theme.layout.desktop;
  return <SafeAreaView testID={testID} style={styles.screen}>
    <FlatList data={lookup.options} keyExtractor={keyExtractor} keyboardShouldPersistTaps="handled"
      contentContainerStyle={styles.content}
      renderItem={({ item }) => <StockRow stock={item} selected={lookup.selected?.symbol === item.symbol} disabled={lookup.searching || lookup.loadingQuote} />}
      ListHeaderComponent={<View>
        <Header compact={compact} />
        <SearchPanel query={lookup.query} onChange={lookup.changeQuery} onSubmit={lookup.submit}
          busy={lookup.searching || lookup.loadingQuote} examples={lookup.examples} compact={compact} />
        {lookup.selected && <QuoteCard selected={lookup.selected} quote={lookup.presentation}
          busy={lookup.loadingQuote} error={lookup.quoteError} onRefresh={lookup.refresh} />}
        <AgentActivity run={lookup.agentRun} busy={lookup.searching || lookup.loadingQuote} sessionId={lookup.sessionId} />
        {lookup.options.length > 0 && <View style={styles.resultsHeader}>
          <View style={styles.resultsCopy}><Text accessibilityRole="header" style={styles.heading}>{copy.results}</Text>
            <Text style={styles.hint}>{copy.resultHint}</Text></View>
          <Text style={styles.count}>{copy.count(lookup.options.length)}</Text>
        </View>}
      </View>}
      ListEmptyComponent={lookup.searching ? <StatusPanel busy title={copy.searching} /> :
        lookup.error ? <StatusPanel error title={lookup.error} /> :
        lookup.searched ? <StatusPanel title={copy.noResults} message={copy.noResultsBody} /> :
        <StatusPanel title={copy.emptyTitle} message={copy.emptyBody} />}
      ListFooterComponent={<View style={styles.footer}>
        <Text style={styles.footerText}>{copy.footer}</Text><Text style={styles.footerText}>{copy.footerRight}</Text>
      </View>} />
  </SafeAreaView>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.canvas },
  content: { width: '100%', maxWidth: theme.layout.maxWidth, alignSelf: 'center', paddingHorizontal: theme.space.xl, paddingBottom: theme.space.xxl },
  resultsHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: theme.space.lg,
    marginTop: theme.space.sm, marginBottom: theme.space.md },
  resultsCopy: { flex: 1, gap: theme.space.xs },
  heading: { color: theme.colors.ink, fontFamily: theme.font.body, fontSize: theme.size.title, fontWeight: theme.weight.medium },
  hint: { color: theme.colors.muted, fontFamily: theme.font.body, fontSize: theme.size.small },
  count: { color: theme.colors.muted, fontFamily: theme.font.mono, fontSize: theme.size.tiny },
  footer: { borderTopColor: theme.colors.line, borderTopWidth: theme.layout.border, marginTop: theme.space.xxl,
    paddingTop: theme.space.xl, flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', gap: theme.space.sm },
  footerText: { color: theme.colors.muted, fontFamily: theme.font.body, fontSize: theme.size.tiny },
});
