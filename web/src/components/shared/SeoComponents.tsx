// SEO/meta components — ported from the Next.js reference's seo/ directory.
// Currently only contains JsonLd (structured-data script tag).

interface JsonLdData {
  [key: string]: unknown;
}

interface JsonLdProps {
  data: JsonLdData | JsonLdData[];
}

/**
 * Renders a JSON-LD structured data script tag. In the Next.js reference this
 * worked in server components; in this SPA it renders the same script tag on
 * the client. Escapes `<` so a `</script>` inside the data can't break out.
 */
export function JsonLd({ data }: JsonLdProps) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, "\\u003c") }}
    />
  );
}
