import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { AppDataProvider } from "./components/AppData";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import { ActivityPage } from "./pages/Activity";
import { LoginPage, RegisterPage } from "./pages/Auth";
import { ChatPage } from "./pages/Chat";
import { MemoriesPage } from "./pages/Memories";
import { RemindersPage } from "./pages/Reminders";
import { SettingsPage } from "./pages/Settings";
import { TasksPage } from "./pages/Tasks";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const location = useLocation();
  if (user === undefined) {
    return (
      <div className="fullscreen-center">
        <Spinner label="Loading…" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <AppDataProvider key={user.id}>{children}</AppDataProvider>;
}

function GuestOnly({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (user) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
      <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        {/* Both chat routes render the same ChatPage instance, so a new conversation
            can move from / to /c/:id without remounting (and dropping a voice session). */}
        <Route index element={<ChatPage />} />
        <Route path="c/:conversationId" element={<ChatPage />} />
        <Route path="memories" element={<MemoriesPage />} />
        <Route path="reminders" element={<RemindersPage />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
