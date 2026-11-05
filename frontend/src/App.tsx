import { Navigate, Route, Routes } from 'react-router'
import AppLayout from './components/AppLayout.tsx'
import { GuestOnly, RequireAdmin, RequireAuth } from './components/RouteGuards.tsx'
import AdminPage from './pages/AdminPage.tsx'
import ChatPage from './pages/ChatPage.tsx'
import DocumentsPage from './pages/DocumentsPage.tsx'
import LoginPage from './pages/LoginPage.tsx'
import RegisterPage from './pages/RegisterPage.tsx'
import SettingsPage from './pages/SettingsPage.tsx'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
      <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
      <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/documents" element={<DocumentsPage />} />
        {/* One route with an optional segment: creating a conversation keeps ChatPage (and its stream) mounted. */}
        <Route path="/chat/:conversationId?" element={<ChatPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/admin" element={<RequireAdmin><AdminPage /></RequireAdmin>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
