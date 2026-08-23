import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/api/auth";
import { AdminStreamProvider } from "@/api/stream";
import { Layout } from "@/components/Layout";
import "@/styles.css";

import { LoginPage } from "@/pages/Login";
import { DashboardPage } from "@/pages/Dashboard";
import { ProvidersPage } from "@/pages/Providers";
import { ProviderDetailPage } from "@/pages/ProviderDetail";
import { BuiltinProvidersPage } from "@/pages/BuiltinProviders";
import { VirtualKeysPage } from "@/pages/VirtualKeys";
import { ModelsPage } from "@/pages/Models";
import { RequestLogsPage } from "@/pages/RequestLogs";
import { ProxyLogsPage } from "@/pages/ProxyLogs";
import { UsagePage } from "@/pages/Usage";
import { AnalyticsPage } from "@/pages/Analytics";
import { BudgetsAlertsPage } from "@/pages/BudgetsAlerts";
import { SettingsPage } from "@/pages/Settings";

// The admin console is dark-only (Dra-style design system).
document.documentElement.classList.add("dark");
localStorage.setItem("wiwi.theme", "dark");

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false },
  },
});

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { authed } = useAuth();
  return authed ? children : <Navigate to="/login" replace />;
}

function AppRoutes() {
  const { authed } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={authed ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AdminStreamProvider>
              <Layout />
            </AdminStreamProvider>
          </RequireAuth>
        }
      >
        <Route path="/" element={<DashboardPage />} />
        <Route path="/providers" element={<ProvidersPage />} />
        <Route path="/providers/:name" element={<ProviderDetailPage />} />
        <Route path="/builtin-providers" element={<BuiltinProvidersPage />} />
        <Route path="/keys" element={<VirtualKeysPage />} />
        <Route path="/models" element={<ModelsPage />} />
        <Route path="/request-logs" element={<RequestLogsPage />} />
        <Route path="/proxy-logs" element={<ProxyLogsPage />} />
        <Route path="/usage" element={<UsagePage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/budgets" element={<BudgetsAlertsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter basename={import.meta.env.BASE_URL}>
          <AppRoutes />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
