"use client";

import { useRef, useState } from "react";
import { ImagePlus, Loader2, X } from "lucide-react";
import { apiClientFetch, ApiError } from "@/lib/api-client";
import { resolveImageUrl } from "@/lib/utils";

interface UploadImagesResponse {
  urls: string[];
}

export function ImageGalleryUploader({
  images,
  onChange,
  uploadUrl = "/api/uploads/images",
  maxImages = 10,
}: {
  images: string[];
  onChange: (images: string[]) => void;
  /** Which endpoint to POST files to -- admin and USER hosts are two separate,
   *  never-merged auth systems, so they use different (equally validated) routes. */
  uploadUrl?: string;
  maxImages?: number;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    setError("");

    const files = Array.from(fileList);
    if (images.length + files.length > maxImages) {
      setError(`A listing can have at most ${maxImages} images (${images.length} already added).`);
      if (inputRef.current) inputRef.current.value = "";
      return;
    }

    setUploading(true);
    try {
      const formData = new FormData();
      files.forEach((file) => formData.append("files", file));
      const result = await apiClientFetch<UploadImagesResponse>(uploadUrl, {
        method: "POST",
        body: formData,
      });
      onChange([...images, ...result.urls]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to upload images");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function removeImage(index: number) {
    onChange(images.filter((_, i) => i !== index));
  }

  return (
    <div>
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
        {images.map((src, i) => (
          <div key={src + i} className="group relative aspect-square overflow-hidden rounded-xl ring-1 ring-slate-200 dark:ring-slate-700">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={resolveImageUrl(src)} alt={`Room photo ${i + 1}`} className="h-full w-full object-cover" />
            <button
              type="button"
              onClick={() => removeImage(i)}
              title="Remove image"
              className="absolute right-1 top-1 flex h-6 w-6 items-center justify-center rounded-full bg-black/60 text-white opacity-0 transition-opacity group-hover:opacity-100"
            >
              <X className="h-3.5 w-3.5" />
            </button>
            {i === 0 && (
              <span className="absolute bottom-1 left-1 rounded-full bg-primary-900/80 px-2 py-0.5 text-[10px] font-semibold text-white">
                Cover
              </span>
            )}
          </div>
        ))}

        {images.length < maxImages && (
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
            className="flex aspect-square flex-col items-center justify-center gap-1.5 rounded-xl border-2 border-dashed border-slate-200 text-slate-400 transition-colors hover:border-primary-300 hover:text-primary-600 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:hover:border-primary-500/50"
          >
            {uploading ? <Loader2 className="h-5 w-5 animate-spin" /> : <ImagePlus className="h-5 w-5" />}
            <span className="text-[11px] font-semibold">{uploading ? "Uploading" : "Add Photos"}</span>
          </button>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        multiple
        hidden
        onChange={(e) => handleFiles(e.target.files)}
      />

      {error && <p className="mt-2 text-xs font-medium text-accent-600">{error}</p>}
      <p className="mt-2 text-[11px] text-slate-400 dark:text-slate-500">
        JPG, PNG, WEBP or GIF, up to 8MB each. First photo is used as the cover image.
      </p>
    </div>
  );
}
