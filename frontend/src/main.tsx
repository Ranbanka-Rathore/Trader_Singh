import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.tsx'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1, // Only retry once, not 3 times (prevents long loading ghost states)
      retryDelay: 1000,
      staleTime: 5000, // Data is fresh for 5 seconds
      refetchOnWindowFocus: true,
    },
    mutations: {
      retry: 0,
    }
  }
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
