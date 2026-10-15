// Enterprise — marketing page for teams running the gateway at scale. The
// shared showcase band carries the hero, proof strip, and capability cards, so
// this page only supplies the page-level rhythm around it. The band is also
// rendered by the landing page, which is what keeps the two from drifting.

import { EnterpriseShowcase } from "@/components/landing/EnterpriseShowcase";

export function EnterprisePage() {
  return (
    <div className="mx-auto max-w-6xl pb-16">
      <EnterpriseShowcase
        headingLevel="h1"
        secondaryCta={{ label: "Read the docs", to: "/docs" }}
      />
    </div>
  );
}
