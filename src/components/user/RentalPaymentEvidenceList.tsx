"use client";

import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { EvidenceArtifact } from "@/lib/types";
import { downloadRentalPaymentEvidence, listRentalPaymentEvidence } from "@/lib/user-api";

/** ZR-PAY-002 Section 5.1 [View document] -- shared between the tenant and
 *  recipient views (both are authorized to see a record's own evidence,
 *  Section 11's "Upload payment evidence" row for either side). */
export function RentalPaymentEvidenceList({ recordId }: { recordId: number }) {
  const [artifacts, setArtifacts] = useState<EvidenceArtifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [openingId, setOpeningId] = useState<number | null>(null);

  useEffect(() => {
    listRentalPaymentEvidence(recordId)
      .then(setArtifacts)
      .catch(() => setArtifacts([]))
      .finally(() => setLoading(false));
  }, [recordId]);

  async function handleOpen(artifact: EvidenceArtifact) {
    setOpeningId(artifact.id);
    try {
      const blob = await downloadRentalPaymentEvidence(recordId, artifact.id);
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener,noreferrer");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } finally {
      setOpeningId(null);
    }
  }

  if (loading) return null;
  if (artifacts.length === 0) return <p className="text-xs text-slate-400">No proof of payment uploaded.</p>;

  return (
    <div className="space-y-1.5">
      {artifacts.map((a) => (
        <button
          key={a.id}
          onClick={() => handleOpen(a)}
          disabled={openingId === a.id}
          className="flex items-center gap-1.5 text-xs font-semibold text-primary-700 hover:underline disabled:opacity-60 dark:text-primary-300"
        >
          <FileText className="h-3.5 w-3.5" aria-hidden="true" /> {openingId === a.id ? "Opening..." : `View document (${a.originalFilename})`}
        </button>
      ))}
    </div>
  );
}
