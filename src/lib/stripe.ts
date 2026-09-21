import { loadStripe, Stripe } from "@stripe/stripe-js";

/** loadStripe() must only ever be called once per publishable key -- calling
 *  it on every render/mount would open a fresh connection to Stripe each
 *  time. Module-level singleton, same "resolve once, reuse the promise"
 *  pattern Stripe's own docs recommend. */
let stripePromise: Promise<Stripe | null> | null = null;

export function getStripe(): Promise<Stripe | null> {
  const key = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY;
  if (!key) return Promise.resolve(null);
  if (!stripePromise) stripePromise = loadStripe(key);
  return stripePromise;
}
