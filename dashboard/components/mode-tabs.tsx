import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";


type ModeOption = {
  label: string;
  value: string;
};

const DEFAULT_OPTIONS: ModeOption[] = [
  { label: "Paper", value: "paper" },
  { label: "Live", value: "live" }
];

export function ModeTabs({
  pathname,
  activeMode,
  options = DEFAULT_OPTIONS
}: {
  pathname: string;
  activeMode: string;
  options?: ModeOption[];
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((option) => {
        const active = option.value === activeMode;

        return (
          <Link
            key={option.value}
            href={`${pathname}?mode=${option.value}`}
            className={cn(
              "rounded-full border px-3 py-2 text-sm transition",
              active ? "border-cyan-300/20 bg-cyan-300/12 text-white" : "border-white/10 bg-white/[0.03] text-white/65"
            )}
          >
            {option.label}
          </Link>
        );
      })}
      <Badge variant="neutral">Lane filter</Badge>
    </div>
  );
}
