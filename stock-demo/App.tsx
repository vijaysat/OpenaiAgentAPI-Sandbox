import React from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { StockScreen } from './src/screens/StockScreen';

export interface AppProps { readonly testID?: string }

export default function App({ testID }: AppProps) {
  return <SafeAreaProvider><StockScreen testID={testID} /></SafeAreaProvider>;
}
