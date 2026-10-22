import {
  BadgeCheck,
  ClipboardList,
  CreditCard,
  FileText,
  House,
  KeyRound,
  MapPin,
  MessagesSquare,
  Search,
  ShieldCheck,
  UserRound,
  type LucideIcon,
} from "lucide-react";

/**
 * Content for the Ask Zoiko home screen (shown before the first message).
 * Wording lives here so it can change without touching the layout.
 */

/** TEMPORARY: cut out of the designer's mockup. Replace with the designer's
 *  own transparent avatar file when it arrives (same path, or update here). */
export const ASSISTANT_ILLUSTRATION: string | null = "/assistant/ask-zoiko-avatar.webp";

export const HOME_GREETING = {
  title: "Hello! I'm Ask Zoiko",
  subtitle: "Your Zoiko Rooms assistant.",
  body: "I can help you find a room, list a property, understand payments, and get guidance on requirements in your country.",
  question: "How can I help you today?",
  tagline: ["Find.", "List.", "Live Better."],
};

export type CardTone = "blue" | "red";

export interface HomeCategory {
  title: string;
  subtitle: string;
  /** Sent as the user's message when the card is chosen. */
  prompt: string;
  icon: LucideIcon;
  tone: CardTone;
}

export const HOME_CATEGORIES: HomeCategory[] = [
  { title: "Find a room", subtitle: "Search, filter and application guidance", prompt: "Help me find a room", icon: Search, tone: "blue" },
  { title: "List a room", subtitle: "How to list and manage your property", prompt: "How do I list my property?", icon: House, tone: "red" },
  { title: "Payments & listing fees", subtitle: "Understand how payments work", prompt: "How do payments and listing fees work?", icon: CreditCard, tone: "blue" },
  { title: "Verification & safety", subtitle: "ID, property and host verification", prompt: "How does ID, property and host verification work?", icon: ShieldCheck, tone: "red" },
  { title: "Rules & compliance", subtitle: "Local housing requirements and tenant rights", prompt: "What are the local housing requirements and tenant rights?", icon: FileText, tone: "blue" },
  { title: "Account help", subtitle: "Navigating your dashboard", prompt: "Help me navigate my dashboard", icon: UserRound, tone: "red" },
];

export interface PopularQuestion {
  question: string;
  icon: LucideIcon;
}

/** The first six are shown on the home screen; "See all" shows the rest too. */
export const POPULAR_QUESTIONS: PopularQuestion[] = [
  { question: "How do I find a room near me?", icon: MapPin },
  { question: "How does verification work?", icon: ShieldCheck },
  { question: "What documents do I need to list?", icon: House },
  { question: "What are the house rules?", icon: FileText },
  { question: "How do payments work?", icon: CreditCard },
  { question: "How do I manage my booking?", icon: UserRound },
  { question: "What's the status of my application?", icon: ClipboardList },
  { question: "What payments do I owe?", icon: CreditCard },
  { question: "Show my occupancy details", icon: UserRound },
  { question: "Where can I follow my room requests?", icon: MessagesSquare },
  { question: "What is a Room Passport?", icon: BadgeCheck },
  { question: "How is my personal data protected?", icon: KeyRound },
];

export const POPULAR_QUESTIONS_SHOWN = 6;
