// EmailVerificationBanner — shows a yellow banner when the logged-in user's
// email is unverified, with a "Resend Email" button. Uses the project's auth
// context (useAuth) for the user object. The resend calls /auth/verify-email
// via the API base.

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/api/auth";
import { Button } from "@/components/ui";

const API_BASE = "/api";

export function EmailVerificationBanner() {
  const { user } = useAuth();
  const [isResending, setIsResending] = useState(false);

  // The wiwi user model is username/password based and has no emailVerified
  // flag, so this banner renders nothing by default. It is kept for parity
  // with pages ported from the reference that place it in their layout.
  if (!user) {
    return null;
  }
  const emailVerified = (user as { emailVerified?: boolean }).emailVerified;
  if (emailVerified !== false) {
    return null;
  }

  const handleResendVerification = async () => {
    setIsResending(true);
    try {
      const res = await fetch(`${API_BASE}/auth/verify-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email: user.username,
          callbackURL: `${window.location.origin}/console?emailVerified=true`,
        }),
      });
      if (!res.ok) {
        throw new Error("Failed to send verification email");
      }
    } catch {
      // best-effort
    } finally {
      setIsResending(false);
    }
  };

  return (
    <div className="border border-yellow-700/30 bg-yellow-900/20 px-4 py-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <div className="flex-1">
            <p className="text-sm text-yellow-200">
              <strong>Your email is unverified.</strong> Please check your inbox
              and click the verification link to access all features.
            </p>
          </div>
        </div>
        <div className="ml-4">
          <Button
            variant="outline"
            onClick={handleResendVerification}
            disabled={isResending}
            className="border-yellow-700 text-yellow-200 hover:bg-yellow-800/30"
          >
            {isResending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isResending ? "Sending..." : "Resend Email"}
          </Button>
        </div>
      </div>
    </div>
  );
}
