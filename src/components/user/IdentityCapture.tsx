"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Camera, RefreshCw, ShieldCheck, Upload } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { documentTypeLabel } from "@/lib/identity-documents";
import { IdentityDocumentType } from "@/lib/types";
import { errorMessage } from "@/lib/user-api";
import { IdentityPack, IdentitySession, submitFaceMatch } from "@/lib/identity";

/**
 * Identity check, on Zoiko's own screens: choose the document, photograph it (or upload a clear picture of it) so
 * the face on it can be seen, take one selfie, then submit both together. The server compares the two faces and the
 * result comes straight back. Nothing is read from the document, the photos are not stored, and nothing here can
 * mark anyone verified -- only the server's comparison can.
 */

type Phase = "document" | "document-photo" | "selfie" | "review";

const PHASE_COPY = {
  "document-photo": {
    title: "Photograph your identity document",
    hint: "Show the side with your photo. Place the original on a flat, dark surface, fit all four corners in the frame, and avoid glare. You can also upload a clear picture of it.",
    facing: "environment" as const,
  },
  selfie: {
    title: "Take a selfie",
    hint: "Face the camera in good light, looking straight ahead. Remove sunglasses or a hat, and make sure only you are in the picture.",
    facing: "user" as const,
  },
};

export function IdentityCapture({ session: initial, pack, onSubmitted, onExit }: {
  session: IdentitySession;
  pack: IdentityPack;
  onSubmitted: (session: IdentitySession) => void;
  onExit: () => void;
}) {
  // After "we need clearer photos" the person starts the photos again, keeping the document they chose.
  const resubmitting = initial.state === "ACTION_REQUIRED";
  const [documentType, setDocumentType] = useState(initial.documentType || "");
  const [phase, setPhase] = useState<Phase>(initial.documentType ? "document-photo" : "document");
  const [photos, setPhotos] = useState<{ document?: Blob; selfie?: Blob }>({});
  const [previews, setPreviews] = useState<{ document?: string; selfie?: string }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => () => Object.values(previews).forEach((url) => url && URL.revokeObjectURL(url)), [previews]);

  const steps: Phase[] = ["document", "document-photo", "selfie", "review"];
  const position = Math.max(0, steps.indexOf(phase));

  function keep(kind: "document" | "selfie", photo: Blob) {
    setPhotos((current) => ({ ...current, [kind]: photo }));
    setPreviews((current) => {
      if (current[kind]) URL.revokeObjectURL(current[kind]!);
      return { ...current, [kind]: URL.createObjectURL(photo) };
    });
    setPhase(kind === "document" ? "selfie" : "review");
  }

  async function submit() {
    if (!photos.document || !photos.selfie) return;
    setBusy(true);
    setError("");
    try {
      onSubmitted(await submitFaceMatch(initial.id, documentType, photos.document, photos.selfie));
    } catch (err) {
      setError(errorMessage(err, "We couldn't check your photos. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        Step {position + 1} of {steps.length}
      </p>
      {error && (
        <div role="alert" className="rounded-xl bg-accent-50 px-4 py-3 text-sm text-accent-700 ring-1 ring-accent-200 dark:bg-accent-500/10 dark:text-accent-300 dark:ring-accent-500/20">
          {error}
        </div>
      )}
      {resubmitting && phase !== "review" && initial.message && (
        <div className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">{initial.message}</div>
      )}

      {phase === "document" && (
        <DocumentChoice pack={pack} value={documentType} onChange={setDocumentType} onBack={onExit}
          onNext={() => setPhase("document-photo")} />
      )}

      {(phase === "document-photo" || phase === "selfie") && (
        <CameraCapture
          key={phase}
          title={PHASE_COPY[phase].title}
          hint={PHASE_COPY[phase].hint}
          facing={PHASE_COPY[phase].facing}
          frame={phase === "selfie" ? "face" : "document"}
          allowUpload={phase === "document-photo"}
          busy={busy}
          onBack={() => setPhase(phase === "document-photo" ? "document" : "document-photo")}
          onPhoto={(photo) => keep(phase === "selfie" ? "selfie" : "document", photo)}
        />
      )}

      {phase === "review" && (
        <div className="space-y-4">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Check that your face is clearly visible in both pictures, then submit. We compare the face on your
            {" "}{documentTypeLabel[documentType as IdentityDocumentType] ?? "document"} with your selfie.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {(["document", "selfie"] as const).map((kind) => (
              <figure key={kind} className="space-y-1">
                <div className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded-xl bg-slate-100 dark:bg-slate-800">
                  {previews[kind] && (
                    // eslint-disable-next-line @next/next/no-img-element -- local blob preview; next/image cannot load it
                    <img src={previews[kind]} alt="" className="h-full w-full object-cover" />
                  )}
                </div>
                <figcaption className="flex items-center justify-between text-xs text-slate-500">
                  {kind === "selfie" ? "Selfie" : "Identity document"}
                  <button type="button" className="font-semibold text-primary-700 hover:underline dark:text-primary-300"
                    onClick={() => setPhase(kind === "selfie" ? "selfie" : "document-photo")}>
                    Retake
                  </button>
                </figcaption>
              </figure>
            ))}
          </div>
          <p className="flex items-start gap-2 text-xs text-slate-500">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            Your photos are compared on our server and are not stored.
          </p>
          <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-between">
            <Button variant="ghost" disabled={busy} onClick={() => setPhase("selfie")}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back</Button>
            <Button loading={busy} onClick={() => void submit()}>Verify my identity</Button>
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
          </label>
        ))}
      </fieldset>
      <p className="text-xs text-slate-500">Choose a document with your photo on it. Only the face on it is compared with your selfie.</p>
      <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-between">
        <Button variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" aria-hidden="true" /> Save and exit</Button>
        <Button disabled={!value} onClick={onNext}><Camera className="h-4 w-4" aria-hidden="true" /> Continue</Button>
      </div>
    </div>
  );
}

/** Live camera with a framing guide; falls back to the device's camera app
 *  (file input with `capture`) when the browser can't open the camera. */
function CameraCapture({ title, hint, facing, frame, allowUpload = false, busy, onBack, onPhoto }: {
  title: string; hint: string; facing: "environment" | "user"; frame: "document" | "face"; allowUpload?: boolean; busy: boolean;
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
              {allowUpload && (
                <label className="relative inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-primary-700 ring-1 ring-primary-200 focus-within:ring-2 focus-within:ring-primary-400 dark:text-primary-300 dark:ring-primary-500/30">
                  <Upload className="h-4 w-4" aria-hidden="true" /> Upload a picture
                  <input type="file" accept="image/jpeg,image/png" className="absolute inset-0 h-full w-full cursor-pointer opacity-0" onChange={fromDeviceCamera} />
                </label>
              )}
              {cameraState !== "live" && (
                <label className="relative inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-primary-700 ring-1 ring-primary-200 focus-within:ring-2 focus-within:ring-primary-400 dark:text-primary-300 dark:ring-primary-500/30">
                  <Upload className="h-4 w-4" aria-hidden="true" /> Use device camera
                  <input type="file" accept="image/jpeg,image/png" capture={facing} className="absolute inset-0 h-full w-full cursor-pointer opacity-0" onChange={fromDeviceCamera} />
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
