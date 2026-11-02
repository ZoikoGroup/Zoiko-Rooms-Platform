"use client";

import { useCallback, useEffect, useState } from "react";

/** The assistant's own language. It is independent of the rest of the app. */
export type ChatLanguage = "EN" | "DE" | "FR" | "ES" | "PT";

export const CHAT_LANGUAGES: Array<{ code: ChatLanguage; label: string }> = [
  { code: "EN", label: "English" },
  { code: "DE", label: "Deutsch" },
  { code: "FR", label: "Français" },
  { code: "ES", label: "Español" },
  { code: "PT", label: "Português" },
];

const STORAGE_KEY = "zoiko-user-chat-language";

const isChatLanguage = (value: string | null): value is ChatLanguage =>
  CHAT_LANGUAGES.some((l) => l.code === value);

/** Chat UI strings. English is the key; a missing entry falls back to English. */
const DICTIONARY: Record<Exclude<ChatLanguage, "EN">, Record<string, string>> = {
  DE: {
    Language: "Sprache",
    "More options": "Weitere Optionen",
    "New conversation": "Neue Unterhaltung",
    "Contact admin": "Administrator kontaktieren",
    "Tell me about verification": "Erzählen Sie mir etwas über die Verifizierung",
    "How do payments work?": "Wie funktionieren Zahlungen?",
    "What is a Room Passport?": "Was ist ein Room Passport?",
    "How do I list a room?": "Wie inseriere ich ein Zimmer?",
    "How do I apply for a room?": "Wie bewerbe ich mich um ein Zimmer?",
    "Can I talk to support?": "Kann ich mit dem Support sprechen?",
    "When do hosts get paid?": "Wann werden Gastgeber bezahlt?",
    "What is escrow and how does it protect me?": "Was ist Treuhand und wie schützt sie mich?",
    "How does the security deposit work?": "Wie funktioniert die Kaution?",
    "What fees does Zoiko Rooms charge?": "Welche Gebühren erhebt Zoiko Rooms?",
    "How long does verification take?": "Wie lange dauert die Verifizierung?",
    "What documents do I need to get verified?": "Welche Dokumente brauche ich zur Verifizierung?",
    "What is Right to Rent?": "Was ist Right to Rent?",
    "What do I need to publish a listing?": "Was brauche ich, um eine Anzeige zu veröffentlichen?",
    "How do I manage applications from renters?": "Wie verwalte ich Bewerbungen von Mietern?",
    "How do hosts get paid?": "Wie werden Gastgeber bezahlt?",
    "How do I search for a room?": "Wie suche ich ein Zimmer?",
    "What happens after I apply?": "Was passiert nach meiner Bewerbung?",
    "How long can I stay?": "Wie lange kann ich bleiben?",
  },
  FR: {
    Language: "Langue",
    "More options": "Plus d'options",
    "New conversation": "Nouvelle conversation",
    "Contact admin": "Contacter l'administrateur",
    "Tell me about verification": "Parlez-moi de la vérification",
    "How do payments work?": "Comment fonctionnent les paiements ?",
    "What is a Room Passport?": "Qu'est-ce qu'un Room Passport ?",
    "How do I list a room?": "Comment publier une chambre ?",
    "How do I apply for a room?": "Comment postuler pour une chambre ?",
    "Can I talk to support?": "Puis-je parler au support ?",
    "When do hosts get paid?": "Quand les hôtes sont-ils payés ?",
    "What is escrow and how does it protect me?": "Qu'est-ce que le séquestre et comment me protège-t-il ?",
    "How does the security deposit work?": "Comment fonctionne le dépôt de garantie ?",
    "What fees does Zoiko Rooms charge?": "Quels frais Zoiko Rooms applique-t-il ?",
    "How long does verification take?": "Combien de temps dure la vérification ?",
    "What documents do I need to get verified?": "Quels documents faut-il pour être vérifié ?",
    "What is Right to Rent?": "Qu'est-ce que le Right to Rent ?",
    "What do I need to publish a listing?": "De quoi ai-je besoin pour publier une annonce ?",
    "How do I manage applications from renters?": "Comment gérer les candidatures des locataires ?",
    "How do hosts get paid?": "Comment les hôtes sont-ils payés ?",
    "How do I search for a room?": "Comment rechercher une chambre ?",
    "What happens after I apply?": "Que se passe-t-il après ma candidature ?",
    "How long can I stay?": "Combien de temps puis-je rester ?",
  },
  ES: {
    Language: "Idioma",
    "More options": "Más opciones",
    "New conversation": "Nueva conversación",
    "Contact admin": "Contactar al administrador",
    "Tell me about verification": "Cuéntame sobre la verificación",
    "How do payments work?": "¿Cómo funcionan los pagos?",
    "What is a Room Passport?": "¿Qué es un Room Passport?",
    "How do I list a room?": "¿Cómo publico una habitación?",
    "How do I apply for a room?": "¿Cómo solicito una habitación?",
    "Can I talk to support?": "¿Puedo hablar con soporte?",
    "When do hosts get paid?": "¿Cuándo cobran los anfitriones?",
    "What is escrow and how does it protect me?": "¿Qué es el depósito en garantía y cómo me protege?",
    "How does the security deposit work?": "¿Cómo funciona la fianza?",
    "What fees does Zoiko Rooms charge?": "¿Qué tarifas cobra Zoiko Rooms?",
    "How long does verification take?": "¿Cuánto tarda la verificación?",
    "What documents do I need to get verified?": "¿Qué documentos necesito para verificarme?",
    "What is Right to Rent?": "¿Qué es Right to Rent?",
    "What do I need to publish a listing?": "¿Qué necesito para publicar un anuncio?",
    "How do I manage applications from renters?": "¿Cómo gestiono las solicitudes de los inquilinos?",
    "How do hosts get paid?": "¿Cómo cobran los anfitriones?",
    "How do I search for a room?": "¿Cómo busco una habitación?",
    "What happens after I apply?": "¿Qué pasa después de solicitar?",
    "How long can I stay?": "¿Cuánto tiempo puedo quedarme?",
  },
  PT: {
    Language: "Idioma",
    "More options": "Mais opções",
    "New conversation": "Nova conversa",
    "Contact admin": "Contactar o administrador",
    "Tell me about verification": "Fale-me sobre a verificação",
    "How do payments work?": "Como funcionam os pagamentos?",
    "What is a Room Passport?": "O que é um Room Passport?",
    "How do I list a room?": "Como anuncio um quarto?",
    "How do I apply for a room?": "Como me candidato a um quarto?",
    "Can I talk to support?": "Posso falar com o suporte?",
    "When do hosts get paid?": "Quando os anfitriões são pagos?",
    "What is escrow and how does it protect me?": "O que é a custódia e como me protege?",
    "How does the security deposit work?": "Como funciona a caução?",
    "What fees does Zoiko Rooms charge?": "Que taxas cobra a Zoiko Rooms?",
    "How long does verification take?": "Quanto tempo demora a verificação?",
    "What documents do I need to get verified?": "Que documentos preciso para ser verificado?",
    "What is Right to Rent?": "O que é o Right to Rent?",
    "What do I need to publish a listing?": "O que preciso para publicar um anúncio?",
    "How do I manage applications from renters?": "Como giro as candidaturas dos inquilinos?",
    "How do hosts get paid?": "Como os anfitriões são pagos?",
    "How do I search for a room?": "Como procuro um quarto?",
    "What happens after I apply?": "O que acontece depois de me candidatar?",
    "How long can I stay?": "Quanto tempo posso ficar?",
  },
};

export function translateChat(language: ChatLanguage, text: string): string {
  return language === "EN" ? text : DICTIONARY[language][text] ?? text;
}

/** The chat's language, remembered per browser. */
export function useChatLanguage() {
  const [language, setLanguageState] = useState<ChatLanguage>("EN");

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (isChatLanguage(stored)) setLanguageState(stored);
    } catch {
      // storage unavailable: the picker still works for this visit
    }
  }, []);

  const setLanguage = useCallback((code: ChatLanguage) => {
    setLanguageState(code);
    try {
      window.localStorage.setItem(STORAGE_KEY, code);
    } catch {
      // ignore
    }
  }, []);

  const t = useCallback((text: string) => translateChat(language, text), [language]);

  return { language, setLanguage, t };
}
