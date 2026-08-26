import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/api/auth";
import { AdminStreamProvider } from "@/api/stream";
import { AdminLayout } from "@/components/Layout";
import { PublicLayout } from "@/components/PublicLayout";
import { RequireAdmin, RequireUser } from "@/components/guards";
import "@/styles.css";

import { LoginPage } from "@/pages/Login";
import { SignupPage } from "@/pages/Signup";
import { LandingPage } from "@/pages/Landing";
import { PlaygroundPage } from "@/pages/Playground";
import { ModelsCatalogPage } from "@/pages/ModelsCatalog";
import { DocsPage } from "@/pages/Docs";
import { UsersPage } from "@/pages/Users";

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

// The console is dark-only (Dra-style design system).
document.documentElement.classList.add("dark");
localStorage.setItem("wiwi.theme", "dark");

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false },
  },
});

function AppRoutes() {
  const { user, loading } = useAuth();
  if (loading) return null;
  return (
    <Routes>
      {/* Public front */}
      <Route element={<PublicLayout />}>
        <Route path="/" element={<LandingPage />} />
        <Route path="/models" element={<ModelsCatalogPage />} />
        <Route path="/docs" element={<DocsPage />} />
      </Route>

      {/* Playground is gated but lives outside the admin shell */}
      <Route
        path="/playground"
        element={
          <RequireUser>
            <PlaygroundPage />
          </RequireUser>
        }
      />

      {/* Auth */}
      <Route path="/login" element={user ? <Navigate to="/app" replace /> : <LoginPage />} />
      <Route path="/signup" element={user ? <Navigate to="/app" replace /> : <SignupPage />} />

      {/* Guarded admin shell */}
      <Route
        element={
          <RequireUser>
            <AdminStreamProvider>
              <AdminLayout />
            </AdminStreamProvider>
          </RequireUser>
        }
      >
        <Route path="/app" element={<DashboardPage />} />
        <Route path="/app/keys" element={<VirtualKeysPage />} />
        <Route path="/app/models" element={<ModelsPage />} />
        <Route path="/app/request-logs" element={<RequestLogsPage />} />
        <Route path="/app/usage" element={<UsagePage />} />
        <Route path="/app/analytics" element={<AnalyticsPage />} />
        <Route path="/app/budgets" element={<BudgetsAlertsPage />} />
        <Route
          path="/app/providers"
          element={
            <RequireAdmin>
              <ProvidersPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/app/providers/:name"
          element={
            <RequireAdmin>
              <ProviderDetailPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/app/builtin-providers"
          element={
            <RequireAdmin>
              <BuiltinProvidersPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/app/proxy-logs"
          element={
            <RequireAdmin>
              <ProxyLogsPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/app/settings"
          element={
            <RequireAdmin>
              <SettingsPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/app/users"
          element={
            <RequireAdmin>
              <UsersPage />
            </RequireAdmin>
          }
        />
      </Route>

      {/* legacy flat-path redirects */}
      <Route path="/keys" element={<Navigate to="/app/keys" replace />} />
      <Route path="/providers" element={<Navigate to="/app/providers" replace />} />
      <Route path="/models-config" element={<Navigate to="/app/models" replace />} />
      <Route path="/request-logs" element={<Navigate to="/app/request-logs" replace />} />
      <Route path="/usage" element={<Navigate to="/app/usage" replace />} />
      <Route path="/analytics" element={<Navigate to="/app/analytics" replace />} />
      <Route path="/budgets" element={<Navigate to="/app/budgets" replace />} />
      <Route path="/settings" element={<Navigate to="/app/settings" replace />} />
      <Route path="/proxy-logs" element={<Navigate to="/app/proxy-logs" replace />} />
      <Route path="/builtin-providers" element={<Navigate to="/app/builtin-providers" replace />} />
      <Route path="/dashboard" element={<Navigate to="/app" replace />} />
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
