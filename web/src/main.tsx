import { StrictMode, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, clearToken } from "@/api/client";
import { AuthProvider, useAuth } from "@/api/auth";
import { AdminStreamProvider } from "@/api/stream";
import { AdminLayout } from "@/components/Layout";
import { PublicLayout } from "@/components/PublicLayout";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { RouteFallback } from "@/components/RouteFallback";
import { RequireAdmin, RequireUser } from "@/components/guards";
import "@/styles.css";

const LoginPage = lazy(() => import("@/pages/Login").then((m) => ({ default: m.LoginPage })));
const SignupPage = lazy(() => import("@/pages/Signup").then((m) => ({ default: m.SignupPage })));
const LandingPage = lazy(() => import("@/pages/Landing").then((m) => ({ default: m.LandingPage })));
const PlaygroundPage = lazy(() => import("@/pages/Playground").then((m) => ({ default: m.PlaygroundPage })));
const ModelsCatalogPage = lazy(() => import("@/pages/ModelsCatalog").then((m) => ({ default: m.ModelsCatalogPage })));
const DocsPage = lazy(() => import("@/pages/Docs").then((m) => ({ default: m.DocsPage })));
const PricingPage = lazy(() => import("@/pages/Pricing").then((m) => ({ default: m.PricingPage })));
const AboutPage = lazy(() => import("@/pages/About").then((m) => ({ default: m.AboutPage })));
const ContactPage = lazy(() => import("@/pages/Contact").then((m) => ({ default: m.ContactPage })));
const EnterprisePage = lazy(() => import("@/pages/Enterprise").then((m) => ({ default: m.EnterprisePage })));
const ComparePage = lazy(() => import("@/pages/Compare").then((m) => ({ default: m.ComparePage })));
const CompareDetailPage = lazy(() => import("@/pages/CompareDetail").then((m) => ({ default: m.CompareDetailPage })));
const LegalPage = lazy(() => import("@/pages/Legal").then((m) => ({ default: m.LegalPage })));
const ChangelogPage = lazy(() => import("@/pages/Changelog").then((m) => ({ default: m.ChangelogPage })));
const BlogPage = lazy(() => import("@/pages/Blog").then((m) => ({ default: m.BlogPage })));
const BlogDetailPage = lazy(() => import("@/pages/BlogDetail").then((m) => ({ default: m.BlogDetailPage })));
const BrandPage = lazy(() => import("@/pages/Brand").then((m) => ({ default: m.BrandPage })));

const ReliabilityPage = lazy(() => import("@/pages/Reliability").then((m) => ({ default: m.ReliabilityPage })));

const IntegrationsPage = lazy(() => import("@/pages/Integrations").then((m) => ({ default: m.IntegrationsPage })));
const GuidesPage = lazy(() => import("@/pages/Guides").then((m) => ({ default: m.GuidesPage })));
const GuideDetailPage = lazy(() => import("@/pages/GuideDetail").then((m) => ({ default: m.GuideDetailPage })));
const MigrationPage = lazy(() => import("@/pages/Migration").then((m) => ({ default: m.MigrationPage })));
const MigrationDetailPage = lazy(() => import("@/pages/MigrationDetail").then((m) => ({ default: m.MigrationDetailPage })));
const OpenSourcePage = lazy(() => import("@/pages/OpenSource").then((m) => ({ default: m.OpenSourcePage })));
const TokenCostCalculatorPage = lazy(() => import("@/pages/TokenCostCalculator").then((m) => ({ default: m.TokenCostCalculatorPage })));

const OnboardingPage = lazy(() => import("@/pages/Onboarding").then((m) => ({ default: m.OnboardingPage })));
const ProductsPage = lazy(() => import("@/pages/Products").then((m) => ({ default: m.ProductsPage })));
const UsersPage = lazy(() => import("@/pages/Users").then((m) => ({ default: m.UsersPage })));
const AuditLogPage = lazy(() => import("@/pages/AuditLog").then((m) => ({ default: m.AuditLogPage })));
const NotFoundPage = lazy(() => import("@/pages/NotFound").then((m) => ({ default: m.NotFoundPage })));

const DashboardPage = lazy(() => import("@/pages/Dashboard").then((m) => ({ default: m.DashboardPage })));
const ProvidersPage = lazy(() => import("@/pages/Providers").then((m) => ({ default: m.ProvidersPage })));
const ProviderDetailPage = lazy(() => import("@/pages/ProviderDetail").then((m) => ({ default: m.ProviderDetailPage })));
const BuiltinProvidersPage = lazy(() => import("@/pages/BuiltinProviders").then((m) => ({ default: m.BuiltinProvidersPage })));
const OAuthProvidersPage = lazy(() => import("@/pages/OAuthProviders").then((m) => ({ default: m.OAuthProvidersPage })));
const OAuthProviderPage = lazy(() => import("@/pages/OAuthProviders").then((m) => ({ default: m.OAuthProviderPage })));
const OAuthAccountDetailPage = lazy(() => import("@/pages/OAuthAccountDetail").then((m) => ({ default: m.OAuthAccountDetailPage })));
const WorkBuddyAccountsPage = lazy(() => import("@/pages/WorkBuddyAccounts").then((m) => ({ default: m.WorkBuddyAccountsPage })));
const AccountLoginPage = lazy(() => import("@/pages/AccountLogin").then((m) => ({ default: m.AccountLoginPage })));
const HermesPage = lazy(() => import("@/pages/Hermes").then((m) => ({ default: m.HermesPage })));
const HermesOAuthPage = lazy(() => import("@/pages/HermesOAuth").then((m) => ({ default: m.HermesOAuthPage })));
const VirtualKeysPage = lazy(() => import("@/pages/VirtualKeys").then((m) => ({ default: m.VirtualKeysPage })));
const ModelsPage = lazy(() => import("@/pages/Models").then((m) => ({ default: m.ModelsPage })));
const CombosPage = lazy(() => import("@/pages/Combos").then((m) => ({ default: m.CombosPage })));
const RequestLogsPage = lazy(() => import("@/pages/RequestLogs").then((m) => ({ default: m.RequestLogsPage })));
const ProxyLogsPage = lazy(() => import("@/pages/ProxyLogs").then((m) => ({ default: m.ProxyLogsPage })));
const UsagePage = lazy(() => import("@/pages/Usage").then((m) => ({ default: m.UsagePage })));
const AnalyticsPage = lazy(() => import("@/pages/Analytics").then((m) => ({ default: m.AnalyticsPage })));
const BudgetsAlertsPage = lazy(() => import("@/pages/BudgetsAlerts").then((m) => ({ default: m.BudgetsAlertsPage })));
const SettingsPage = lazy(() => import("@/pages/Settings").then((m) => ({ default: m.SettingsPage })));

// The console is dark-only (Dra-style design system).
document.documentElement.classList.add("dark");
localStorage.setItem("wiwi.theme", "dark");

// A 401 means the session/key expired — drop stale credentials and bounce to
// /login, otherwise pages would keep rendering their idle empty states for a
// tab that no longer has a valid session (AUDIT #338).
function redirectOnAuthError(e: unknown) {
  if (!(e instanceof ApiError) || e.status !== 401) return;
  clearToken();
  if (!window.location.pathname.endsWith("/login")) {
    window.location.assign(`${import.meta.env.BASE_URL}login`);
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false },
  },
  queryCache: new QueryCache({ onError: redirectOnAuthError }),
  mutationCache: new MutationCache({ onError: redirectOnAuthError }),
});

// Legacy redirects must carry the query string and hash across.
//
// `<Navigate to="/console/providers" />` DROPS both: a string `to` is run
// through `parsePath`, which only splits off a `search`/`hash` that is present
// *in that string*. A deep link such as `/providers?type=openrouter` (the
// Built-in Providers "Add account" button) therefore arrived at
// `/console/providers` with no query at all, so Providers' `?type=` preset —
// the whole purpose of that link — never applied (AUDIT #206).
function legacyTarget(pathname: string, loc: { search: string; hash: string }) {
  return { pathname, search: loc.search, hash: loc.hash };
}

// Legacy redirect: /app/* → /console/* (and bare /app → /console).
function LegacyAppRedirect() {
  const { "*": rest } = useParams();
  const loc = useLocation();
  return <Navigate to={legacyTarget(rest ? `/console/${rest}` : "/console", loc)} replace />;
}

function AppRoutes() {
  const { user, loading } = useAuth();
  // Legacy flat paths are redirects, and a redirect must not drop the query
  // string a deep link arrived with (AUDIT #206).
  const loc = useLocation();
  if (loading) return null;
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
      {/* Public front */}
      <Route element={<PublicLayout />}>
        <Route path="/" element={<LandingPage />} />
        <Route path="/models" element={<ModelsCatalogPage />} />
        <Route path="/docs" element={<DocsPage />} />
        {/* Docs is a single page with in-page sections, so any deeper path
            (/docs/cursor, a bookmarked /docs/config#streaming, …) would
            otherwise fall through the catch-all to the marketing landing. */}
        <Route path="/docs/*" element={<Navigate to="/docs" replace />} />
        <Route path="/pricing" element={<PricingPage />} />
        <Route path="/about" element={<AboutPage />} />
        <Route path="/contact" element={<ContactPage />} />
        <Route path="/enterprise" element={<EnterprisePage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/compare/:slug" element={<CompareDetailPage />} />
        <Route path="/legal" element={<LegalPage />} />
        <Route path="/changelog" element={<ChangelogPage />} />
        <Route path="/blog" element={<BlogPage />} />
        <Route path="/blog/:slug" element={<BlogDetailPage />} />
        <Route path="/brand" element={<BrandPage />} />
        <Route path="/reliability" element={<ReliabilityPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="/guides" element={<GuidesPage />} />
        <Route path="/guides/:slug" element={<GuideDetailPage />} />
        <Route path="/migration" element={<MigrationPage />} />
        <Route path="/migration/:slug" element={<MigrationDetailPage />} />
        <Route path="/open-source" element={<OpenSourcePage />} />
        <Route path="/token-cost-calculator" element={<TokenCostCalculatorPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/products" element={<ProductsPage />} />
        {/* Unknown paths get a real 404 inside the public chrome instead of a
            silent redirect to the landing page. */}
        <Route path="*" element={<NotFoundPage />} />
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
        <Route
          path="/console/combos"
          element={
            <RequireAdmin>
              <CombosPage />
            </RequireAdmin>
          }
        />
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
          path="/console/oauth/:provider"
          element={
            <RequireAdmin>
              <OAuthProviderPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/oauth/:provider/:account"
          element={
            <RequireAdmin>
              <OAuthAccountDetailPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/login"
          element={
            <RequireAdmin>
              <AccountLoginPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/workbuddy"
          element={
            <RequireAdmin>
              <WorkBuddyAccountsPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/hermes"
          element={
            <RequireAdmin>
              <HermesPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/console/hermes-oauth"
          element={
            <RequireAdmin>
              <HermesOAuthPage />
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
          path="/console/audit"
          element={
            <RequireAdmin>
              <AuditLogPage />
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

      {/* legacy flat-path redirects — a redirect must not drop the query
          string a deep link arrived with, e.g. /providers?type=openrouter
          from the Built-in Providers "Add account" button (AUDIT #206). */}
      <Route path="/keys" element={<Navigate to={legacyTarget("/console/keys", loc)} replace />} />
      <Route path="/providers" element={<Navigate to={legacyTarget("/console/providers", loc)} replace />} />
      <Route path="/models-config" element={<Navigate to={legacyTarget("/console/models", loc)} replace />} />
      <Route path="/request-logs" element={<Navigate to={legacyTarget("/console/request-logs", loc)} replace />} />
      <Route path="/usage" element={<Navigate to={legacyTarget("/console/usage", loc)} replace />} />
      <Route path="/analytics" element={<Navigate to={legacyTarget("/console/analytics", loc)} replace />} />
      <Route path="/budgets" element={<Navigate to={legacyTarget("/console/budgets", loc)} replace />} />
      <Route path="/settings" element={<Navigate to={legacyTarget("/console/settings", loc)} replace />} />
      <Route path="/proxy-logs" element={<Navigate to={legacyTarget("/console/proxy-logs", loc)} replace />} />
      <Route path="/builtin-providers" element={<Navigate to={legacyTarget("/console/builtin-providers", loc)} replace />} />
      <Route path="/dashboard" element={<Navigate to={legacyTarget("/console", loc)} replace />} />
      </Routes>
    </Suspense>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter basename={import.meta.env.BASE_URL}>
          <ErrorBoundary>
            <AppRoutes />
          </ErrorBoundary>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
