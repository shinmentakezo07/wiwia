// ReferralHandler — reads a `ref` query param and POSTs it to the referral
// endpoint so the server can set a tracking cookie. Mounted at the root;
// silently fails. Uses react-router's useSearchParams.

import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

const API_BASE = "/api";

export function ReferralHandler() {
  const [searchParams] = useSearchParams();

  useEffect(() => {
    const ref = searchParams.get("ref");
    if (ref) {
      fetch(`${API_BASE}/referral`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ ref }),
      }).catch(() => {
        // Silently fail — referral tracking is not critical.
      });
    }
  }, [searchParams]);

  return null;
}
