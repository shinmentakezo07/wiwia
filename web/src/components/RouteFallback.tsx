/** Suspense fallback for lazily-loaded routes — holds the frame instead of
 *  flashing an empty page while a chunk downloads. */
export function RouteFallback() {
  return (
    <div className="flex min-h-[50vh] items-center justify-center">
      <span
        aria-label="Loading"
        className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-blue-400"
      />
    </div>
  );
}
