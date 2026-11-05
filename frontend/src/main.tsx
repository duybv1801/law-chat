import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import App from './App.tsx'
import { ApiError, setUnauthorizedHandler } from './api/client.ts'
import { ME_QUERY_KEY, resetAuthState } from './hooks/useAuth.ts'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 4xx are not transient: do not retry them (401 is already refreshed by the api client).
      retry: (failureCount, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) &&
        failureCount < 2,
      refetchOnWindowFocus: false,
    },
  },
})

// Session could not be refreshed: forget all cached data; RequireAuth then redirects to /login.
// Skipped when no user is known (e.g. the anonymous `/auth/me` probe on /login): nothing to reset.
setUnauthorizedHandler(() => {
  if (queryClient.getQueryData(ME_QUERY_KEY) == null) return
  resetAuthState(queryClient)
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
