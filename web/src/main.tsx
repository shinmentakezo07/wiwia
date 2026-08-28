import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
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
import { PricingPage } from "@/pages/Pricing";
import { AboutPage } from "@/pages/About";
import { ContactPage } from "@/pages/Contact";
import { EnterprisePage } from "@/pages/Enterprise";
import { ComparePage } from "@/pages/Compare";
import { LegalPage } from "@/pages/Legal";
import { ChangelogPage } from "@/pages/Changelog";
import { BlogPage } from "@/pages/Blog";
import { BrandPage } from "@/pages/Brand";
import { PartnersPage } from "@/pages/Partners";
import { ReliabilityPage } from "@/pages/Reliability";
import { RankingsPage } from "@/pages/Rankings";
import { IntegrationsPage } from "@/pages/Integrations";
import { GuidesPage } from "@/pages/Guides";
import { MigrationPage } from "@/pages/Migration";
import { OpenSourcePage } from "@/pages/OpenSource";
import { CopilotCostCalculatorPage } from "@/pages/CopilotCostCalculator";
import { TokenCostCalculatorPage } from "@/pages/TokenCostCalculator";
import { TimelinePage } from "@/pages/Timeline";
import { TemplatesPage } from "@/pages/Templates";
import { AgentsPage } from "@/pages/Agents";
import { AppsPage } from "@/pages/Apps";
import { SSOPage } from "@/pages/SSO";
import { ReferralsPage } from "@/pages/Referrals";
import { ShipPage } from "@/pages/Ship";
import { ConnectPage } from "@/pages/Connect";
import { OnboardingPage } from "@/pages/Onboarding";
import { ProductsPage } from "@/pages/Products";
import { UsersPage } from "@/pages/Users";

import { DashboardPage } from "@/pages/Dashboard";
import { ProvidersPage } from "@/pages/Providers";
import { ProviderDetailPage } from "@/pages/ProviderDetail";
import { BuiltinProvidersPage } from "@/pages/BuiltinProviders";
import { OAuthProvidersPage } from "@/pages/OAuthProviders";
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

// Legacy redirect: /app/* → /console/* (and bare /app → /console).
function LegacyAppRedirect() {
  const { "*": rest } = useParams();
  return <Navigate to={rest ? `/console/${rest}` : "/console"} replace />;
}

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
        <Route path="/pricing" element={<PricingPage />} />
        <Route path="/about" element={<AboutPage />} />
        <Route path="/contact" element={<ContactPage />} />
        <Route path="/enterprise" element={<EnterprisePage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/legal" element={<LegalPage />} />
        <Route path="/changelog" element={<ChangelogPage />} />
        <Route path="/blog" element={<BlogPage />} />
        <Route path="/brand" element={<BrandPage />} />
        <Route path="/partners" element={<PartnersPage />} />
        <Route path="/reliability" element={<ReliabilityPage />} />
        <Route path="/rankings" element={<RankingsPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="/guides" element={<GuidesPage />} />
        <Route path="/migration" element={<MigrationPage />} />
        <Route path="/open-source" element={<OpenSourcePage />} />
        <Route path="/copilot-cost-calculator" element={<CopilotCostCalculatorPage />} />
        <Route path="/token-cost-calculator" element={<TokenCostCalculatorPage />} />
        <Route path="/timeline" element={<TimelinePage />} />
        <Route path="/templates" element={<TemplatesPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/apps" element={<AppsPage />} />
        <Route path="/sso" element={<SSOPage />} />
        <Route path="/referrals" element={<ReferralsPage />} />
        <Route path="/ship" element={<ShipPage />} />
        <Route path="/connect" element={<ConnectPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/products" element={<ProductsPage />} />
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
      <Route path="/login" element={user ? <Navigate to="/console" replace /> : <LoginPage />} />
      <Route path="/signup" element={user ? <Navigate to="/console" replace /> : <SignupPage />} />

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
        <Route path="/console" element={<DashboardPage />} />
        <Route path="/console/keys" element={<VirtualKeysPage />} />
        <Route path="/console/models" element={<ModelsPage />} />
        <Route path="/console/request-logs" element={<RequestLogsPage />} />
        <Route path="/console/usage" element={<UsagePage />} />
        <Route path="/console/analytics" element={<AnalyticsPage />} />
        <Route path="/console/budgets" element={<BudgetsAlertsPage />} />
        <Route
          path="/console/providers"
          element={
            <RequireAdmin>
              <ProvidersPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/providers/:name"
          element={
            <RequireAdmin>
              <ProviderDetailPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/builtin-providers"
          element={
            <RequireAdmin>
              <BuiltinProvidersPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/oauth"
          element={
            <RequireAdmin>
              <OAuthProvidersPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/proxy-logs"
          element={
            <RequireAdmin>
              <ProxyLogsPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/settings"
          element={
            <RequireAdmin>
              <SettingsPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/users"
          element={
            <RequireAdmin>
              <UsersPage />
            </RequireAdmin>
          }
        />
      </Route>

      {/* legacy /app/* → /console/* redirect (old admin base path) */}
      <Route path="/app/*" element={<LegacyAppRedirect />} />

      {/* legacy flat-path redirects */}
      <Route path="/keys" element={<Navigate to="/console/keys" replace />} />
      <Route path="/providers" element={<Navigate to="/console/providers" replace />} />
      <Route path="/models-config" element={<Navigate to="/console/models" replace />} />
      <Route path="/request-logs" element={<Navigate to="/console/request-logs" replace />} />
      <Route path="/usage" element={<Navigate to="/console/usage" replace />} />
      <Route path="/analytics" element={<Navigate to="/console/analytics" replace />} />
      <Route path="/budgets" element={<Navigate to="/console/budgets" replace />} />
      <Route path="/settings" element={<Navigate to="/console/settings" replace />} />
      <Route path="/proxy-logs" element={<Navigate to="/console/proxy-logs" replace />} />
      <Route path="/builtin-providers" element={<Navigate to="/console/builtin-providers" replace />} />
      <Route path="/dashboard" element={<Navigate to="/console" replace />} />
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
