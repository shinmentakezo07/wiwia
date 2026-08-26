// Hero suggestions for the playground empty state. Adapted from the
// llmgateway-ref playground's hero-suggestions.ts — trimmed to the three text
// groups that make sense for a chat-only playground (no image gen / video / canvas).

export const heroSuggestionGroups = {
  Create: [
    "Write a Python script to analyze CSV data and create visualizations",
    "Create a compelling elevator pitch for a sustainable fashion startup",
    "Explain quantum computing like I'm 12 years old",
    "Design a 7-day workout plan for busy professionals",
    "Write a short mystery story in exactly 100 words",
    "Draft a launch announcement for a new productivity app",
    "Create a weekly meal plan for a vegetarian family",
    "Write a persuasive email asking for a project extension",
    "Design a morning routine for better focus and energy",
    "Create a checklist for moving into a new apartment",
    "Write a heartfelt birthday message for a close friend",
    "Draft a social media calendar for a local bakery",
    "Create a budget plan for saving for a vacation",
    "Write a product description for noise-canceling headphones",
    "Design a 30-day habit-building challenge",
    "Create a study schedule for final exams",
  ],
  Explore: [
    "What are trending AI research topics right now?",
    "Summarize the latest news about TypeScript",
    "Find interesting datasets for a side project",
    "Suggest tech blogs to follow for frontend performance",
    "Explain the main arguments for and against remote work",
    "Compare different approaches to personal knowledge management",
    "What should I know before learning machine learning?",
    "Explore the history of open-source software",
    "Explain how electric vehicles affect the power grid",
    "Compare popular note-taking systems for students",
    "What are the tradeoffs between SQL and NoSQL databases?",
    "Explain why some startups choose freemium pricing",
    "What are the biggest challenges in space exploration?",
    "Explore how recommendation algorithms shape media habits",
    "Compare different ways to learn a new language",
    "What are the benefits and risks of biometric authentication?",
  ],
  Code: [
    "Refactor this React component for readability",
    "Write unit tests for a Node.js service",
    "Explain how to debounce an input in React",
    "Show an example of a Zod schema with refinement",
    "Debug this React component and suggest performance improvements",
    "Write a TypeScript utility to group objects by key",
    "Explain how React Server Components work",
    "Write a SQL query to find duplicate email addresses",
    "Design a REST API for a simple task manager",
    "Convert this JavaScript function to strict TypeScript",
    "Explain how to avoid prop drilling in React",
    "Write a Vitest test for an async function",
    "Create a reusable pagination helper",
    "Explain the difference between promises and async/await",
    "Write a custom React hook for local storage",
    "Design a database schema for event registrations",
  ],
} as const;

export type HeroSuggestionGroup = keyof typeof heroSuggestionGroups;

export const heroSuggestionGroupNames = Object.keys(
  heroSuggestionGroups,
) as HeroSuggestionGroup[];

/** Return `count` random suggestions from a group without repeats. */
export function sampleSuggestions(
  items: readonly string[],
  count: number,
): readonly string[] {
  const shuffled = [...items];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j]!, shuffled[i]!];
  }
  return shuffled.slice(0, count);
}
