// SocialAuthButtons — social login buttons (Google + GitHub). Ported from
// the Next.js reference's social-auth-buttons.tsx. The reference used
// better-auth's signIn.social and a WebAuthn abort service; this port keeps
// the UI and wires the buttons to a configurable OAuth callback URL via
// window.location redirect (the common pattern for a self-hosted gateway
// without better-auth). An "signup_disabled" confirmation dialog is preserved.

import { Github, Loader2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

type SocialProvider = "github" | "google";

const PROVIDER_LABELS: Record<SocialProvider, string> = {
  github: "GitHub",
  google: "Google",
};

// Remembers which provider started the OAuth round trip so the login page can
// offer to retry it as an explicit sign-up when no account exists yet.
const PENDING_PROVIDER_KEY = "llmgateway-social-signin-provider";

interface SocialAuthButtonsProps {
  isLoading: boolean;
  setIsLoading: (loading: boolean) => void;
  callbackPath: string;
  errorCallbackPath: string;
  newUserCallbackPath?: string;
  /** Explicitly allow creating a new account (signup pages). */
  requestSignUp?: boolean;
  /** OAuth base URL for initiating social login, e.g. "/api/auth/oauth". */
  oauthBaseUrl?: string;
  /** Whether the GitHub/Google buttons should be shown. */
  enabledProviders?: { github?: boolean; google?: boolean };
}

export function SocialAuthButtons({
  isLoading,
  setIsLoading,
  callbackPath,
  errorCallbackPath,
  newUserCallbackPath,
  requestSignUp,
  oauthBaseUrl = "/api/auth/oauth",
  enabledProviders = { github: true, google: true },
}: SocialAuthButtonsProps) {
  const navigate = useNavigate();

  const [signupDisabledState, setSignupDisabledState] = useState<{
    provider: SocialProvider | null;
  } | null>(() => {
    if (typeof window === "undefined" || requestSignUp) {
      return null;
    }
    const params = new URLSearchParams(window.location.search);
    if (params.get("error") !== "signup_disabled") {
      return null;
    }
    const stored = sessionStorage.getItem(PENDING_PROVIDER_KEY);
    return {
      provider: stored === "github" || stored === "google" ? stored : null,
    };
  });

  // Strip the ?error= param from the URL once we've captured it.
  useState(() => {
    if (!signupDisabledState) return;
    sessionStorage.removeItem(PENDING_PROVIDER_KEY);
    const params = new URLSearchParams(window.location.search);
    if (params.get("error") === "signup_disabled") {
      params.delete("error");
      const query = params.toString();
      navigate(query ? `${window.location.pathname}?${query}` : window.location.pathname, {
        replace: true,
      });
    }
  });

  if (!enabledProviders.github && !enabledProviders.google) {
    return null;
  }

  function buildOAuthUrl(provider: SocialProvider, options?: { requestSignUp?: boolean }) {
    const origin = location.protocol + "//" + location.host;
    const params = new URLSearchParams({
      provider,
      callbackURL: origin + callbackPath,
      errorCallbackURL: origin + errorCallbackPath,
    });
    if (requestSignUp || options?.requestSignUp) {
      params.set("requestSignUp", "true");
    }
    if (newUserCallbackPath) {
      params.set(
        "newUserCallbackURL",
        origin +
          newUserCallbackPath +
          (newUserCallbackPath.includes("?") ? "&" : "?") +
          "signup_method=" +
          provider,
      );
    }
    return `${oauthBaseUrl}?${params.toString()}`;
  }

  function handleSocialSignIn(provider: SocialProvider, options?: { requestSignUp?: boolean }) {
    setIsLoading(true);
    sessionStorage.setItem(PENDING_PROVIDER_KEY, provider);
    // Full-page redirect to the OAuth entrypoint.
    window.location.href = buildOAuthUrl(provider, options);
  }

  const confirmProvider = signupDisabledState?.provider ?? null;

  return (
    <>
      {signupDisabledState && (
        <div className="admin-overlay-enter fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="admin-dialog-enter w-full max-w-md overflow-hidden rounded-2xl border border-white/[0.06] bg-[var(--admin-surface-elevated)] shadow-2xl">
            <div className="px-5 py-4">
              <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">No account found</h3>
              <p className="mt-2 text-[13px] text-[var(--admin-text-muted)]">
                {confirmProvider
                  ? `There is no wiwi account for the ${PROVIDER_LABELS[confirmProvider]} login you used. You need to sign up first — we can create your account with that ${PROVIDER_LABELS[confirmProvider]} login right now.`
                  : "There is no wiwi account for the login you used. You need to sign up first to create one."}
              </p>
            </div>
            <div className="flex justify-end gap-2 border-t border-white/[0.04] px-5 py-3.5">
              <button
                type="button"
                onClick={() => setSignupDisabledState(null)}
                className="admin-btn admin-btn-ghost"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  if (confirmProvider) {
                    handleSocialSignIn(confirmProvider, { requestSignUp: true });
                  } else {
                    navigate("/signup");
                  }
                  setSignupDisabledState(null);
                }}
                className="admin-btn admin-btn-primary"
              >
                {confirmProvider
                  ? `Sign up with ${PROVIDER_LABELS[confirmProvider]}`
                  : "Go to sign up"}
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {enabledProviders.github && (
          <button
            type="button"
            onClick={() => handleSocialSignIn("github")}
            className="admin-btn admin-btn-ghost w-full justify-center disabled:opacity-50"
            disabled={isLoading}
          >
            {isLoading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Github className="mr-2 h-4 w-4" />
            )}
            GitHub
          </button>
        )}
        {enabledProviders.google && (
          <button
            type="button"
            onClick={() => handleSocialSignIn("google")}
            className="admin-btn admin-btn-ghost w-full justify-center disabled:opacity-50"
            disabled={isLoading}
          >
            {isLoading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <svg className="mr-2 h-4 w-4" viewBox="0 0 24 24">
                <path
                  d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
                  fill="#4285F4"
                />
                <path
                  d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                  fill="#34A853"
                />
                <path
                  d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                  fill="#FBBC05"
                />
                <path
                  d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                  fill="#EA4335"
                />
              </svg>
            )}
            Google
          </button>
        )}
      </div>
    </>
  );
}
