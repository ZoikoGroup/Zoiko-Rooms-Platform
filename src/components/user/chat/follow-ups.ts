/**
 * Suggested follow-up questions shown under the latest reply. They are ranked by
 * the topic of the question just asked and never repeat a question already asked.
 */
const TOPICS: Array<{ keywords: RegExp; questions: string[] }> = [
  {
    keywords: /\b(payment|pay|paid|payout|escrow|rent|fee|deposit)s?\b/i,
    questions: [
      "When do hosts get paid?",
      "What is escrow and how does it protect me?",
      "How does the security deposit work?",
      "What fees does Zoiko Rooms charge?",
    ],
  },
  {
    keywords: /\b(verif\w*|passport|identity|id check|right to rent)\b/i,
    questions: [
      "What is a Room Passport?",
      "How long does verification take?",
      "What documents do I need to get verified?",
      "What is Right to Rent?",
    ],
  },
  {
    keywords: /\b(list|listing|host|landlord|provider)\b/i,
    questions: [
      "How do I list a room?",
      "What do I need to publish a listing?",
      "How do I manage applications from renters?",
      "How do hosts get paid?",
    ],
  },
  {
    keywords: /\b(find|search|apply|application|renter|book|move[- ]?in|stay)\b/i,
    questions: [
      "How do I apply for a room?",
      "How do I search for a room?",
      "What happens after I apply?",
      "How long can I stay?",
    ],
  },
];

const GENERAL = [
  "Tell me about verification",
  "How do payments work?",
  "What is a Room Passport?",
  "How do I list a room?",
  "How do I apply for a room?",
  "Can I talk to support?",
];

const normalise = (q: string) => q.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();

/** English follow-up questions (translate them for display with the chat's `t`). */
export function suggestFollowUps(lastQuestion: string, askedQuestions: string[], limit = 3): string[] {
  const asked = new Set([lastQuestion, ...askedQuestions].map(normalise));
  const related = TOPICS.filter((t) => t.keywords.test(lastQuestion)).flatMap((t) => t.questions);
  return [...new Set([...related, ...GENERAL])].filter((q) => !asked.has(normalise(q))).slice(0, limit);
}
