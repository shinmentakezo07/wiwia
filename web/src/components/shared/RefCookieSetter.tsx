// RefCookieSetter — POSTs an org id to the referral endpoint so the server
// can set a referral cookie. Silently fails; referral tracking is not
// critical. Self-contained: no external imports beyond React.

import { useEffect } from "react";

const API_BASE = "/api";

export function RefCookieSetter({ orgId }: { orgId: string }) {
  useEffect(() => {
    fetch(`${API_BASE}/referral`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ ref: orgId }),
    }).catch(() => {
      // Silently fail — referral tracking is not critical.
    });
  }, [orgId]);

  return null;
}
