import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";


export function MetricCard({
  title,
  value,
  description,
  footnote
}: {
  title: string;
  value: string;
  description: string;
  footnote?: ReactNode;
}) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-2xl sm:text-3xl">{value}</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 text-sm text-white/60">
        <p>{description}</p>
        {footnote ? <div className="mt-4">{footnote}</div> : null}
      </CardContent>
    </Card>
  );
}
