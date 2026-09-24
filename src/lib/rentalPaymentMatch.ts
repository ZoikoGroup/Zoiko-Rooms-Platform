import { ObligationRead, RentalPaymentObligation } from "@/lib/types";

/** ZR-PAY-LINK-003 migration matcher: crud/leasing.py:create_agreement
 *  creates exactly one legacy Obligation (models/finance.py) AND one
 *  RentalPaymentObligation for each of an agreement's initial RENT/DEPOSIT
 *  charges, at the same moment; crud/occupancy.py:generate_next_rent_obligation
 *  does the same for each recurring RENT period, scoped by occupancyId
 *  instead of agreementId. Either scope can have an obligation with no
 *  new-rail counterpart at all (a deposit top-up amendment on the
 *  agreement side; a legacy-only recurring obligation predating this
 *  migration on the occupancy side) -- matching within the wrong scope,
 *  or by type alone, would wrongly treat one of those as migrated. Only
 *  the chronologically EARLIEST legacy obligation of a given type, WITHIN
 *  its own scope, is ever the one with a real new-rail counterpart.
 *
 *  Returns null (meaning: render the existing legacy Card/Pay-now UI
 *  unchanged for this one item) whenever no confident match exists --
 *  never a guess. */

function scopeKey(o: { agreementId: number | null; occupancyId: number | null }): string | null {
  if (o.agreementId != null) return `agreement:${o.agreementId}`;
  if (o.occupancyId != null) return `occupancy:${o.occupancyId}`;
  return null;
}

export function matchRentalPaymentObligation(
  legacyObligation: ObligationRead,
  allLegacyObligationsInScope: ObligationRead[],
  rentalPaymentObligationsInScope: RentalPaymentObligation[]
): RentalPaymentObligation | null {
  const targetScope = scopeKey(legacyObligation);
  if (targetScope == null) return null;

  const earliestLegacyOfType = [...allLegacyObligationsInScope]
    .filter((o) => scopeKey(o) === targetScope && o.obligationType === legacyObligation.obligationType)
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt))[0];
  if (!earliestLegacyOfType || earliestLegacyOfType.id !== legacyObligation.id) return null;

  const matchingNewObligations = rentalPaymentObligationsInScope.filter(
    (o) => scopeKey(o) === targetScope && o.obligationType === legacyObligation.obligationType
  );
  if (matchingNewObligations.length === 0) return null;

  return [...matchingNewObligations].sort((a, b) => a.createdAt.localeCompare(b.createdAt))[0];
}
