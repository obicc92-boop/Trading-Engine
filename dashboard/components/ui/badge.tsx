import type { HTMLAttributes } from "react";

import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";


const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium uppercase tracking-[0.18em]",
  {
    variants: {
      variant: {
        neutral: "border-white/10 bg-white/5 text-white/70",
        positive: "border-emerald-400/20 bg-emerald-400/10 text-emerald-200",
        negative: "border-rose-400/20 bg-rose-400/10 text-rose-200",
        accent: "border-cyan-400/20 bg-cyan-400/10 text-cyan-100"
      }
    },
    defaultVariants: {
      variant: "neutral"
    }
  }
);

interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
