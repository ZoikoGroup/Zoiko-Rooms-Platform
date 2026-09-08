"use client";

import Image from "next/image";
import { cn } from "@/lib/utils";

interface AssistantAvatarProps {
  size?: "sm" | "md" | "lg";
  className?: string;
}

const sizes: Record<NonNullable<AssistantAvatarProps["size"]>, string> = {
  sm: "h-7 w-7",
  md: "h-9 w-9",
  lg: "h-12 w-12",
};

/** Brand avatar used for the assistant across the chat panels and launchers. */
export function AssistantAvatar({ size = "sm", className }: AssistantAvatarProps) {
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-primary-100",
        sizes[size],
        className
      )}
    >
      <Image
        src="/zoikorooms-icon-png.png"
        alt=""
        width={48}
        height={48}
        priority
        className="h-full w-full object-cover"
      />
    </span>
  );
}
