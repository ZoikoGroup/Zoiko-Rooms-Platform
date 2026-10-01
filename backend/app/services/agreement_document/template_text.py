"""Verbatim text of the Zoiko Rooms "Global Master Template -- Residential
Occupancy Agreement (Host-Renter Agreement)", jurisdiction-aware, v1.0.

LEGAL CONTENT: every string below is copied word-for-word from the approved
master template document. Do not edit, paraphrase or "fix" wording here
without a new approved template version -- bump TEMPLATE_VERSION when the
text changes, so each generated agreement records exactly which wording it
was produced from (snapshot["document"]["template_version"])."""

TEMPLATE_ID = "ZR-ROA-GLOBAL"
TEMPLATE_VERSION = "1.0"
TEMPLATE_TITLE = "Residential Occupancy Agreement"
TEMPLATE_SUBTITLE = "Host–Renter Agreement"
TEMPLATE_KICKER = ("GLOBAL MASTER TEMPLATE", "JURISDICTION-AWARE")

MANDATORY_LAW_CONTROL_TITLE = "Mandatory-law control"
MANDATORY_LAW_CONTROL = (
    "Mandatory law applicable to the Premises prevails over any inconsistent term. The relevant Jurisdiction Pack "
    "and agreement classification must be applied before this agreement can be marked Ready to Sign or Executed."
)

# (section number, section title, [(clause number, clause title, clause text), ...])
SECTIONS: list[tuple[str, str, list[tuple[int, str, str]]]] = [
    ("02", "Core Agreement Terms", [
        (1, "Parties and legal capacity",
         "This Agreement is entered into between the person identified as the Host in the Agreement Summary and the "
         "person identified as the Renter. The Host and Renter are together the “Parties.” Platform-facing "
         "labels do not determine legal status; the applicable legal relationship is the Agreement Type shown in the "
         "Agreement Summary and Schedule C."),
        (2, "Host authority",
         "The Host represents that the Host has the legal authority required to offer the Premises for occupation and "
         "to enter into this Agreement. Where the Host acts as an agent, property manager, superior tenant, sublessor "
         "or other representative, the Host must have the authority required by applicable law and any superior "
         "agreement."),
        (3, "Premises and rights of occupation",
         "The Premises are the property, room/unit and any exclusive-use or shared-use areas identified in Schedule A. "
         "The Renter receives only the rights of occupation stated in this Agreement and provided by applicable law. "
         "Nothing in this Agreement is intended to misclassify a tenancy, lease, license, lodger arrangement, sublease "
         "or other legal relationship."),
        (4, "Term, commencement and possession",
         "The contractual start date, possession date, fixed-term end date (if any) and renewal/periodic status are "
         "set out in Schedule A. Execution of this Agreement does not override any separate legal requirement for "
         "stamping, witnessing, notarization, registration or another formality."),
        (5, "Rent",
         "The Renter must pay the rent stated in Schedule A to the payee identified there, at the frequency and on the "
         "due date stated there. Any late fee, interest, rent increase or other rent-related charge applies only if and "
         "to the extent permitted by applicable law."),
        (6, "Security deposit and other security",
         "Any security deposit, holding amount, guarantee or other security is governed by Schedule A and applicable "
         "law. Where law requires a deposit to be protected, registered, held by a particular person, accompanied by "
         "prescribed information, limited in amount, or returned within a specified period, those requirements apply "
         "automatically. Zoiko Rooms does not hold rent or security deposits unless a separate service expressly "
         "states otherwise."),
        (7, "Payments and platform role",
         "Zoiko Rooms may provide payment-enablement or transaction-record functionality, but rent and "
         "security-deposit obligations remain between the legally responsible Parties or an authorized third-party "
         "payment provider. Any Zoiko Rooms listing fee is separate from rent, deposit and other sums owed under this "
         "Agreement."),
        (8, "Utilities, taxes and recurring charges",
         "Responsibility for utilities, internet, local taxes, service charges and other recurring costs is set out in "
         "Schedule B. A Party is responsible only for charges allocated to that Party by this Agreement and permitted "
         "by applicable law."),
    ]),
    ("03", "Use, Condition and Property Standards", [
        (9, "Permitted residential use and occupancy",
         "The Renter must use the Premises primarily for the lawful residential purpose stated in Schedule A and comply "
         "with lawful occupancy limits. Named permitted occupants, if any, are identified in Schedule A. The Agreement "
         "does not authorize unlawful overcrowding or a prohibited change of use."),
        (10, "Move-in condition and inventory",
         "The Parties should use the move-in condition record in Schedule B to record the condition of the Premises, "
         "fixtures, furniture, keys/access devices and relevant meter readings. Where supported, timestamped "
         "photographs or video may be linked to the agreement record. Failure to record an item does not waive a "
         "statutory right or obligation."),
        (11, "Repairs, maintenance, habitability and safety",
         "Each Party must perform the repair, maintenance, habitability, health, safety and compliance obligations "
         "imposed on that Party by applicable law. No term of this Agreement transfers, excludes or limits a "
         "non-waivable legal duty."),
        (12, "Damage and cleanliness",
         "The Renter must take reasonable care of the Premises and must not deliberately or negligently cause damage "
         "beyond ordinary wear and tear, subject to applicable law. The Host remains responsible for obligations that "
         "law places on the owner, landlord, licensor or other provider of the accommodation."),
        (13, "Access, inspections and privacy",
         "The Host or an authorized representative may enter the Premises only for a lawful purpose, with any notice "
         "and consent required by applicable law, except where lawful emergency access applies. Nothing in this "
         "Agreement creates a broader right of entry than the law permits."),
        (14, "House rules and common areas",
         "Any property-specific house rules appear in Schedule B. House rules must be reasonable, lawful, consistently "
         "applied and compatible with mandatory housing, consumer, privacy, accessibility and anti-discrimination "
         "protections. A house rule cannot override a non-waivable right."),
        (15, "Guests and additional occupants",
         "Guests and additional occupants are governed by Schedule B and applicable law. Where consent may lawfully be "
         "required, the consent process must not be used to avoid statutory rights or unlawfully discriminate."),
        (16, "Pets, smoking and other property conditions",
         "Any agreed conditions concerning pets, smoking, noise, parking, storage, shared facilities, commercial "
         "activity or similar matters are stated in Schedule B and apply only to the extent permitted by law."),
    ]),
    ("04", "Changes, Subletting and Risk Allocation", [
        (17, "Alterations and installations",
         "The Renter must not make material alterations or installations requiring the Host's consent unless that "
         "consent has been obtained or applicable law provides otherwise. Reasonable adjustments, accessibility "
         "measures and other protected accommodations are governed by applicable law."),
        (18, "Assignment, transfer and subletting",
         "The Renter must not assign, transfer or sublet rights under this Agreement except where permitted by "
         "applicable law and any superior agreement. Where Host consent is lawfully required, the request must be made "
         "to the Host or authorized agent. Zoiko Rooms records the request and response but does not itself grant "
         "property-owner consent."),
        (19, "Host sale, transfer or change of manager",
         "A sale, transfer, appointment of an agent or change of property manager does not remove rights or "
         "protections that applicable law preserves for the Renter. Any required notice of a change of landlord, "
         "payee, service address or manager must be provided in the legally required form."),
        (20, "Insurance and personal property",
         "Each Party is responsible for any insurance allocated to that Party by applicable law or Schedule B. Unless "
         "an insurance product expressly says otherwise, Zoiko Rooms does not insure the Premises, the Renter's "
         "possessions, the Host's property, rent payments or performance of this Agreement."),
        (21, "Loss, interruption and casualty",
         "If the Premises become damaged, unsafe, uninhabitable or unavailable, the Parties' rights concerning repair, "
         "rent reduction, alternative accommodation, termination and compensation are determined by applicable law "
         "and any valid insurance or emergency arrangement."),
        (22, "Compliance with law",
         "Each Party must comply with laws applicable to that Party and the Premises, including any licensing, "
         "registration, building, fire-safety, occupancy, immigration/right-to-rent, tax or similar requirements that "
         "law makes applicable. The platform must not present a completed agreement as proof that every external "
         "legal requirement has been satisfied."),
        (23, "Anti-discrimination and protected rights",
         "Nothing in this Agreement permits conduct prohibited by applicable fair-housing, equality, "
         "anti-discrimination or accessibility law. Any inconsistent property rule or preference is unenforceable to "
         "the extent the law prohibits it."),
        (24, "Communications and notices",
         "Operational messages may be delivered through Zoiko Rooms, email, SMS or another agreed channel. A statutory "
         "or contractual notice is effective only if delivered using a method recognized for that notice in the "
         "applicable jurisdiction. The Parties' formal service details are stated in Schedule A."),
    ]),
    ("05", "Breach, Ending the Agreement and Legal Effect", [
        (25, "Breach and remedies",
         "If a Party breaches this Agreement, the other Party may use only those remedies available under this "
         "Agreement and applicable law. Any required warning, cure period, prescribed form, notice, tribunal process "
         "or court process must be followed."),
        (26, "No self-help eviction or unlawful exclusion",
         "Nothing in this Agreement authorizes lockout, utility disconnection, seizure of property, harassment, "
         "removal of possessions or any other self-help remedy where such conduct is prohibited or restricted by law."),
        (27, "Termination",
         "The circumstances in which this Agreement may be ended, and the notice required, are determined by the "
         "Agreement Type, Schedule C and applicable law. A contractual termination provision does not shorten a "
         "mandatory minimum notice period or remove a statutory ground, defense, hearing or possession process."),
        (28, "Move-out and return of possession",
         "At the end of the lawful occupation period, the Renter must return possession, keys and access credentials "
         "as required by law and this Agreement. The Parties should complete the move-out condition record. Any "
         "deposit deduction or final charge must be itemized and legally permitted."),
        (29, "Abandoned property",
         "Any belongings left at the Premises after the Renter leaves must be handled in accordance with applicable "
         "law, including any notice, storage, disposal or sale requirements."),
        (30, "Mandatory law and non-waivable rights",
         "Mandatory law applicable to the Premises prevails over any inconsistent provision of this Agreement. Nothing "
         "in this Agreement excludes, limits or waives a right or remedy that cannot lawfully be excluded, limited or "
         "waived."),
        (31, "Governing law and forum",
         "This Agreement is governed by the law that mandatorily applies to the occupation of the Premises. Any "
         "contractual choice of law, court, tribunal, mediation or arbitration mechanism stated in Schedule C applies "
         "only to the extent legally permitted and must not remove a mandatory local forum or consumer/housing "
         "protection."),
        (32, "Disputes",
         "The Parties should use any mandatory or agreed dispute process identified in Schedule C. Use of the Zoiko "
         "Rooms messaging, evidence or transaction-record features does not prevent either Party from exercising a "
         "legal right to use a regulator, ombudsman, tribunal, court or other competent body."),
        (33, "Entire agreement and incorporated documents",
         "This Agreement, its schedules, properly incorporated disclosures and any valid signed amendment form the "
         "agreement between the Parties concerning the occupation described here, subject always to mandatory law. A "
         "listing description, chat message or platform screen changes this Agreement only where the law gives it "
         "contractual effect or the Parties validly incorporate it."),
        (34, "Amendments",
         "Any amendment must be made in a form legally sufficient for the relevant term and must identify the "
         "agreement version being changed. The system must retain the executed prior version and create an immutable "
         "amendment history."),
        (35, "Severability and no waiver",
         "If a provision is invalid or unenforceable, it is ineffective only to the extent required by law, without "
         "automatically invalidating the remainder. A delay or failure to enforce a right does not waive it unless "
         "applicable law provides otherwise."),
    ]),
    ("06", "Zoiko Rooms Platform and Digital Execution", [
        (36, "Zoiko Rooms is not a contracting party",
         "Zoiko Rooms is a technology platform and trading name of Zoiko Realty Group. Unless a separate signed "
         "agreement expressly provides otherwise, Zoiko Rooms is not the Host, Renter, owner, landlord, tenant, "
         "licensor, licensee, property manager, guarantor, deposit holder or party to this Agreement."),
        (37, "Verification scope",
         "A Zoiko Rooms verification indicator means only that the platform completed the verification steps "
         "expressly identified in the agreement record. Verification is not a guarantee of title, property condition, "
         "solvency, future conduct, legal compliance or the absence of fraud. Where the platform verifies a Host, the "
         "system should separately record identity, existence of the property and authority to list."),
        (38, "Platform records and evidence",
         "The platform may retain agreement versions, consents, notices, subletting requests, transaction records, "
         "condition evidence and execution events in accordance with its privacy and retention rules. A Party may use "
         "available records as evidence, subject to applicable evidentiary and privacy law."),
        (39, "Electronic records and signatures",
         "Where legally permitted, the Parties consent to receive, review and sign this Agreement electronically. The "
         "validity and required form of an electronic signature, witness, seal, stamp, notarization or registration "
         "are determined by applicable law. The platform must use the execution method required by the applicable "
         "Jurisdiction Pack."),
        (40, "Execution status",
         "The system must distinguish between Draft, Ready to Sign, Partially Signed, Fully Signed, Formalities "
         "Pending, Executed, Effective, Amended, Terminated, Expired and Cancelled/Void (where legally appropriate). "
         "“Signed” alone must not imply that every legal formality has been completed."),
        (41, "Electronic execution evidence",
         "The platform should preserve a tamper-evident execution record linking each signatory to the exact document "
         "version presented for signature. The printable certificate may show an audit reference while sensitive "
         "device/IP data remains in the secured audit log."),
        (42, "Counterparts and copies",
         "Where permitted by applicable law, the Parties may sign counterparts or electronic copies that together form "
         "one agreement. Each Party should receive or be able to retrieve the final executed version and any required "
         "statutory documents."),
    ]),
]

# Callout printed directly after clause 40.
PRODUCTION_CONTROL_TITLE = "Production control"
PRODUCTION_CONTROL = (
    "If stamping, registration, witnessing, notarization, government filing or another legal formality remains "
    "outstanding, the generated agreement should display FORMALITIES PENDING rather than EXECUTED."
)

SCHEDULE_A_TITLE = "Schedule A — Property, Parties and Commercial Terms"
SCHEDULE_B_TITLE = "Schedule B — Condition, Utilities and Property Rules"
SCHEDULE_C_TITLE = "Schedule C — Jurisdiction-Specific Terms and Mandatory Disclosures"
SCHEDULE_D_TITLE = "Schedule D — Signatures and Execution Certificate"

SCHEDULE_C_PACK_TITLE = "System-generated jurisdiction pack"
SCHEDULE_C_PACK_TEXT = (
    "This schedule must be generated from the law applicable to the Premises. It should render only the terms and "
    "disclosures relevant to the transaction and suppress any clause that conflicts with mandatory local law."
)

ORDER_OF_PRECEDENCE_TITLE = "Order of precedence"
ORDER_OF_PRECEDENCE = [
    "Mandatory law",
    "Schedule C",
    "signed Agreement Summary and Schedules A–B",
    "Core Agreement Terms",
    "lawfully incorporated property rules/disclosures.",
]

SCHEDULE_D_INTRO = (
    "By signing below, each Party confirms that the Party has had the opportunity to review the complete Agreement, "
    "including its schedules and mandatory disclosures, and intends to be bound to the extent permitted by applicable "
    "law."
)

DOCUMENT_INTEGRITY_TITLE = "Document integrity"
DOCUMENT_INTEGRITY = (
    "The executed copy must be immutable. Any later change must create a new version or signed amendment rather than "
    "overwrite the executed document."
)

CLOSING_LINE = "Generated and securely recorded through Zoiko Rooms"
CLOSING_SUBLINE = "Zoiko Rooms is a trading name of Zoiko Realty Group"
