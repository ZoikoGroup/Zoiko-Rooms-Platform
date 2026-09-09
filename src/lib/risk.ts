// Risk classification for the Ask Zoiko assistant, per ZR-AI-UX-001 §6.
//
// This is a lightweight, client-side classification used to surface the
// contextual reliance warning (Layer C) for high-consequence topics. The
// authoritative risk routing/authority boundary is enforced server-side (the
// assistant only has read-only, role-scoped tools); this helper only decides
// whether to show an inline "confirm with the authoritative record" warning
// alongside the assistant's answer.

const HIGH_CONSEQUENCE_TERMS: Array<[string, RegExp]> = [
  ["compliance", /\bcomplian\w*\b/i],
  ["right-to-rent", /\bright[\s-]?to[\s-]?rent\b|\bright of rent\b|\bimmigration\b|\bvisa\b|\bborder\b/i],
  ["deposit", /\bdeposit\b/i],
  ["payment", /\bpay\w*\b|\bbill\b|\bcharge\b|\binvoice\b|\bowe\b/i],
  ["agreement", /\bagreement\b|\bcontract\b|\btenan\w*\b|\bsignature\b|\bsign\b/i],
  ["eligibility", /\beligib\w*\b|\bqualif\w*\b|\bapproved?\b|\brejection\b|\breject\b/i],
  ["dispute", /\bdispute\b|\bcomplain\w*\b|\bappeal\b/i],
  ["discrimination", /\bdiscrimination\b|\bharass\w*\b|\bbias\b|\bfair\w*\b/i],
  ["safety", /\bsafe\w*\b|\bdanger\b|\bemergency\b|\bthreat\w*\b|\bcrisis\b|\bwife\b/i],
  ["deadline", /\bdeadline\b|\bnotice period\b|\bnotice\b/i],
  ["eviction", /\bevict\w*\b|\bhomeless\w*\b|\blandlord\b/i],
];

export type RiskClass = "R0" | "R1" | "R2" | "R3" | "R4";

// R3: the user asks the assistant to make a determination or override an
// authoritative outcome.
const DETERMINATION_PATTERN =
  /\b(approve me|override|decide|should i (be|get)|is this (compliant|legal)|am i (eligible|approved)|do i have the right|rule on|make the decision|determine)\b/i;

// R4: immediate danger / safety crisis.
const CRISIS_PATTERN =
  /\b(i'?m (in )?danger|hurting (myself|me)|kill (myself|me)|being (abused|attacked)|physical (danger|threat)|my (life|safety) is (in )?danger)\b/i;

export function classifyRisk(text: string): RiskClass {
  if (!text) return "R0";
  if (CRISIS_PATTERN.test(text)) return "R4";
  if (DETERMINATION_PATTERN.test(text)) return "R3";
  if (HIGH_CONSEQUENCE_TERMS.some(([, re]) => re.test(text))) return "R2";
  return "R0";
}

export function riskTopicName(text: string): string {
  for (const [name, re] of HIGH_CONSEQUENCE_TERMS) {
    if (re.test(text)) return name;
  }
  return "";
}
