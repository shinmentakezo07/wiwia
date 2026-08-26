// SignupMethodTracker — fires a `user_signed_up` analytics event for OAuth
// registrations. The email signup path captures the event inline, but a social
// sign-up redirects away, so the new-user callback lands with
// `?signup_method=<provider>`; this reads that param, emits the event, and
// strips the param so a refresh can't double-count.
//
// In this SPA port there is no PostHog, so the event is dispatched to the
// dataLayer / console only. Uses react-router's useSearchParams.

import { useEffect, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

export function SignupMethodTracker() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const signupMethod = searchParams.get("signup_method");
  const tracked = useRef(false);

  useEffect(() => {
    if (!signupMethod || tracked.current) {
      return;
    }
    tracked.current = true;

    // No PostHog in this port; push to dataLayer if available, else console.
    const event = {
      type: "user_signed_up",
      method: signupMethod,
    };
    const w = window as unknown as { dataLayer?: unknown[] };
    if (typeof window !== "undefined" && Array.isArray(w.dataLayer)) {
      w.dataLayer.push(event);
    } else {
      console.info("[analytics] user_signed_up", event);
    }

    // Strip the param so a refresh can't double-count.
    const params = new URLSearchParams(searchParams.toString());
    params.delete("signup_method");
    const query = params.toString();
    navigate(query ? `?${query}` : location.pathname, { replace: true });
  }, [signupMethod, searchParams, navigate]);

  return null;
}
