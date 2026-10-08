"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Camera, CheckCircle2, RefreshCw, ShieldCheck, Upload } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { documentTypeLabel } from "@/lib/identity-documents";
import { IdentityDocumentType } from "@/lib/types";
import { errorMessage } from "@/lib/user-api";
import {
  CaptureContext,
  IdentityPack,
  IdentitySession,
  captureIdentityPhoto,
  completeIdentityCapture,
} from "@/lib/identity";

/**
 * ZR-IDV-ADR-001 capture, on Zoiko's own screens: choose the document,
 * photograph its front (and back where it has one), take a selfie, then
 * submit. Each photo goes to our backend, which relays it straight to Veriff
 * (never stored); Veriff decides. Nothing here can mark anyone verified.
 */

type Phase = "document" | CaptureContext | "review";

const NO_BACK_SIDE = ["passport", "pan_card"];

const PHASE_COPY: Record<CaptureContext, { title: string; hint: string; facing: "environment" | "user" }> = {
  "document-front": {
    title: "Photograph the front of your document",
    hint: "Place the original document on a flat, dark surface. Fit all four corners in the frame -- no glare, no fingers over the text.",
    facing: "environment",
  },
  "document-back": {
    title: "Now the back of your document",
    hint: "Turn the document over and fit all four corners in the frame.",
    facing: "environment",
  },
  face: {
    title: "Take a selfie",
    hint: "Face the camera in good light. Remove sunglasses or a hat, and keep a neutral expression.",
    facing: "user",
  },
};

function needsBack(documentType: string): boolean {
  return Boolean(documentType) && !NO_BACK_SIDE.includes(documentType);
}

function firstMissing(session: IdentitySession, documentType: string): Phase {
  if (!documentType) return "document";
  const done = new Set(session.captured);
  if (!done.has("document-front")) return "document-front";
  if (needsBack(documentType) && !done.has("document-back")) return "document-back";
  if (!done.has("face")) return "face";
  return "review";
}

export function IdentityCapture({ session: initial, pack, onSubmitted, onExit }: {
  session: IdentitySession;
  pack: IdentityPack;
  onSubmitted: (session: IdentitySession) => void;
  onExit: () => void;
}) {
  const [session, setSession] = useState(initial);
  // A resubmission starts a fresh set of photos, but keeps the chosen document.
  const resubmitting = initial.state === "ACTION_REQUIRED";
  const [documentType, setDocumentType] = useState(initial.documentType || "");
  const [phase, setPhase] = useState<Phase>(() =>
    resubmitting ? (initial.documentType ? "document-front" : "document") : firstMissing(initial, initial.documentType || ""));
  const [previews, setPreviews] = useState<Partial<Record<CaptureContext, string>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => () => Object.values(previews).forEach((url) => url && URL.revokeObjectURL(url)), [previews]);

  const steps: Phase[] = ["document", "document-front", ...(needsBack(documentType) ? ["document-back" as const] : []), "face", "review"];
  const position = Math.max(0, steps.indexOf(phase));

  async function send(context: CaptureContext, photo: Blob) {
    setBusy(true);
    setError("");
    try {
      const updated = await captureIdentityPhoto(session.id, context, photo, context === "document-front" ? documentType : "");
      setSession(updated);
      setPreviews((current) => {
        if (current[context]) URL.revokeObjectURL(current[context]!);
        return { ...current, [context]: URL.createObjectURL(photo) };
      });
      setPhase(firstMissing({ ...updated, captured: updated.captured }, documentType));
    } catch (err) {
      setError(errorMessage(err, "We couldn't use this photo. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    setBusy(true);
    setError("");
    try {
      onSubmitted(await completeIdentityCapture(session.id));
    } catch (err) {
      setError(errorMessage(err, "We couldn't submit your photos. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        Photo {Math.min(position, steps.length - 2) || 1} of {steps.length - 2}
      </p>
      {error && (
        <div role="alert" className="rounded-xl bg-accent-50 px-4 py-3 text-sm text-accent-700 ring-1 ring-accent-200 dark:bg-accent-500/10 dark:text-accent-300 dark:ring-accent-500/20">
          {error}
        </div>
      )}
      {resubmitting && phase !== "review" && session.message && (
        <div className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">{session.message}</div>
      )}

      {phase === "document" && (
        <DocumentChoice
          pack={pack} value={documentType} onChange={setDocumentType} onBack={onExit}
          onNext={() => setPhase("document-front")}
        />
      )}

      {(phase === "document-front" || phase === "document-back" || phase === "face") && (
        <CameraCapture
          key={phase}
          title={PHASE_COPY[phase].title}
          hint={PHASE_COPY[phase].hint}
          facing={PHASE_COPY[phase].facing}
          frame={phase === "face" ? "face" : "document"}
          busy={busy}
          onBack={() => setPhase(phase === "document-front" ? "document" : phase === "document-back" ? "document-front"
            : needsBack(documentType) ? "document-back" : "document-front")}
          onPhoto={(photo) => void send(phase, photo)}
        />
      )}

      {phase === "review" && (
        <div className="space-y-4">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Check your photos are sharp and readable, then submit. Our verification partner checks that your
            {" "}{documentTypeLabel[documentType as IdentityDocumentType] ?? "document"} is genuine and that it&apos;s you.
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            {(["document-front", ...(needsBack(documentType) ? ["document-back"] : []), "face"] as CaptureContext[]).map((context) => (
              <figure key={context} className="space-y-1">
                <div className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded-xl bg-slate-100 dark:bg-slate-800">
                  {previews[context] ? (
                    // eslint-disable-next-line @next/next/no-img-element -- local blob preview; next/image cannot load it
                    <img src={previews[context]} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <CheckCircle2 className="h-8 w-8 text-emerald-600" aria-hidden="true" />
                  )}
                </div>
                <figcaption className="flex items-center justify-between text-xs text-slate-500">
                  {context === "face" ? "Selfie" : context === "document-front" ? "Front" : "Back"}
                  <button type="button" className="font-semibold text-primary-700 hover:underline dark:text-primary-300" onClick={() => setPhase(context)}>
                    Retake
                  </button>
                </figcaption>
              </figure>
            ))}
          </div>
          <p className="flex items-start gap-2 text-xs text-slate-500">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            Your photos are sent securely to our verification partner and are not stored by Zoiko Rooms.
          </p>
          <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-between">
            <Button variant="ghost" onClick={() => setPhase("face")}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
            <Button loading={busy} onClick={() => void submit()}>Submit for verification</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function DocumentChoice({ pack, value, onChange, onBack, onNext }: {
  pack: IdentityPack; value: string; onChange: (v: string) => void; onBack: () => void; onNext: () => void;
}) {
  return (
    <div className="space-y-4">
      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-primary-900 dark:text-white">Which document will you use?</legend>
        {pack.acceptedDocumentTypes.map((type) => (
          <label key={type} className={`flex cursor-pointer items-center gap-3 rounded-xl p-3 text-sm ring-1 ${
            value === type ? "bg-primary-50 ring-primary-300 dark:bg-primary-500/10 dark:ring-primary-500/40" : "ring-slate-200 dark:ring-slate-700"
          }`}>
            <input type="radio" name="identity-document" checked={value === type} onChange={() => onChange(type)} />
            <span className="text-primary-900 dark:text-white">{documentTypeLabel[type as IdentityDocumentType] ?? type}</span>
            {!needsBack(type) && <span className="text-xs text-slate-400">front only</span>}
          </label>
        ))}
      </fieldset>
      <p className="text-xs text-slate-500">Use the original document -- not a photocopy, a screenshot or a photo of a screen.</p>
      <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-between">
        <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Save and exit</Button>
        <Button disabled={!value} onClick={onNext}><Camera className="h-4 w-4" aria-hidden="true" /> Continue</Button>
      </div>
    </div>
  );
}

/** Live camera with a framing guide; falls back to the device's camera app
 *  (file input with `capture`) when the browser can't open the camera. */
function CameraCapture({ title, hint, facing, frame, busy, onBack, onPhoto }: {
  title: string; hint: string; facing: "environment" | "user"; frame: "document" | "face"; busy: boolean;
  onBack: () => void; onPhoto: (photo: Blob) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [cameraState, setCameraState] = useState<"starting" | "live" | "unavailable">("starting");
  const [shot, setShot] = useState<{ blob: Blob; url: string } | null>(null);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const start = useCallback(async () => {
    stop();
    setCameraState("starting");
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraState("unavailable");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: facing, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => undefined);
      }
      setCameraState("live");
    } catch {
      setCameraState("unavailable");
    }
  }, [facing, stop]);

  useEffect(() => {
    void start();
    return stop;
  }, [start, stop]);

  useEffect(() => () => { if (shot) URL.revokeObjectURL(shot.url); }, [shot]);

  function take() {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext("2d");
    if (!context) return;
    if (facing === "user") {
      // The preview is mirrored for a natural selfie; the photo is not.
      context.translate(canvas.width, 0);
      context.scale(-1, 1);
    }
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) return;
      stop();
      setShot({ blob, url: URL.createObjectURL(blob) });
    }, "image/jpeg", 0.92);
  }

  function retake() {
    setShot(null);
    void start();
  }

  function fromDeviceCamera(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) setShot({ blob: file, url: URL.createObjectURL(file) });
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-primary-900 dark:text-white">{title}</h3>
        <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{hint}</p>
      </div>

      <div className="relative mx-auto aspect-[4/3] w-full max-w-xl overflow-hidden rounded-2xl bg-slate-900">
        {shot ? (
          // eslint-disable-next-line @next/next/no-img-element -- local blob preview; next/image cannot load it
          <img src={shot.url} alt="Your photo" className="h-full w-full object-contain" />
        ) : (
          <>
            <video
              ref={videoRef}
              playsInline
              muted
              className={`h-full w-full object-cover ${facing === "user" ? "-scale-x-100" : ""} ${cameraState === "live" ? "" : "invisible"}`}
            />
            {cameraState === "live" && (
              <div aria-hidden="true" className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <div className={frame === "face"
                  ? "h-[70%] aspect-[3/4] rounded-[50%] border-4 border-white/80 shadow-[0_0_0_9999px_rgba(0,0,0,0.35)]"
                  : "w-[85%] aspect-[1.586] rounded-xl border-4 border-white/80 shadow-[0_0_0_9999px_rgba(0,0,0,0.35)]"} />
              </div>
            )}
            {cameraState === "starting" && (
              <p className="absolute inset-0 flex items-center justify-center text-sm text-white/80" role="status">Starting camera...</p>
            )}
            {cameraState === "unavailable" && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-6 text-center text-sm text-white/90">
                <Camera className="h-8 w-8" aria-hidden="true" />
                <p>We couldn&apos;t open your camera here. Allow camera access in your browser, or use your device&apos;s camera below.</p>
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-between">
        <Button variant="ghost" disabled={busy} onClick={() => { stop(); onBack(); }}>
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back
        </Button>
        <div className="flex flex-col gap-2 sm:flex-row">
          {shot ? (
            <>
              <Button variant="outline" disabled={busy} onClick={retake}><RefreshCw className="h-4 w-4" aria-hidden="true" /> Retake</Button>
              <Button loading={busy} onClick={() => onPhoto(shot.blob)}>Use this photo</Button>
            </>
          ) : (
            <>
              {cameraState !== "live" && (
                <label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-primary-700 ring-1 ring-primary-200 focus-within:ring-2 focus-within:ring-primary-400 dark:text-primary-300 dark:ring-primary-500/30">
                  <Upload className="h-4 w-4" aria-hidden="true" /> Use device camera
                  <input type="file" accept="image/jpeg,image/png" capture={facing} className="sr-only" onChange={fromDeviceCamera} />
                </label>
              )}
              {cameraState === "unavailable" && (
                <Button variant="outline" onClick={() => void start()}><RefreshCw className="h-4 w-4" aria-hidden="true" /> Try camera again</Button>
              )}
              <Button disabled={cameraState !== "live"} onClick={take}><Camera className="h-4 w-4" aria-hidden="true" /> Take photo</Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
