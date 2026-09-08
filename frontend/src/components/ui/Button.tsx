import { ButtonHTMLAttributes, forwardRef } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "accent" | "outline" | "ghost" | "white";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  fullWidth?: boolean;
}

const variantClasses: Record<Variant, string> = {
  primary:
    "bg-primary-700 text-white shadow-lg shadow-primary-900/25 hover:bg-primary-800 hover:shadow-xl hover:shadow-primary-900/30 focus-visible:ring-primary-300",
  accent:
    "bg-accent-600 text-white shadow-lg shadow-accent-700/30 hover:bg-accent-700 hover:shadow-xl hover:shadow-accent-700/35 focus-visible:ring-accent-300",
  outline:
    "border-2 border-primary-700 text-primary-700 hover:bg-primary-50 focus-visible:ring-primary-200 dark:text-primary-300 dark:border-primary-400/60 dark:hover:bg-primary-500/10",
  ghost:
    "text-primary-700 hover:bg-primary-50 focus-visible:ring-primary-200 dark:text-primary-300 dark:hover:bg-primary-500/10",
  white:
    "bg-white text-primary-800 shadow-lg shadow-black/10 hover:shadow-xl hover:bg-primary-50 focus-visible:ring-white",
};

const sizeClasses: Record<Size, string> = {
  sm: "text-sm px-3.5 py-2 gap-1.5",
  md: "text-sm px-5 py-2.5 gap-2",
  lg: "text-base px-7 py-3.5 gap-2.5",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant = "primary", size = "md", loading, fullWidth, disabled, children, ...props },
    ref
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          "relative inline-flex items-center justify-center rounded-full font-semibold",
          "transition-all duration-300 ease-out active:scale-[0.97]",
          "focus-visible:outline-none focus-visible:ring-4",
          "disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:shadow-none",
          "cursor-pointer",
          variantClasses[variant],
          sizeClasses[size],
          fullWidth && "w-full",
          className
        )}
        {...props}
      >
        {loading && <Loader2 className="h-4 w-4 animate-spin" />}
        {children}
      </button>
    );
  }
);

Button.displayName = "Button";
