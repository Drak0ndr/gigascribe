import React from 'react'
import { createRoot } from 'react-dom/client'
import { MantineProvider, createTheme } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import '@mantine/core/styles.css'
import '@mantine/notifications/styles.css'
import App from './App'
import './styles.css'

const theme = createTheme({
  fontFamily: 'Inter, "Segoe UI", Arial, sans-serif',
  primaryColor: 'indigo',
  defaultRadius: 'md',
  headings: { fontFamily: 'Inter, "Segoe UI", Arial, sans-serif', fontWeight: '600' },
  colors: { indigo: ['#eef0ff','#e2e5ff','#c5c9ff','#a0a9f5','#808be7','#6d76da','#5a61c5','#4d54ab','#41498c','#373e73'] },
})

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark">
      <Notifications position="top-right" />
      <App />
    </MantineProvider>
  </React.StrictMode>,
)
