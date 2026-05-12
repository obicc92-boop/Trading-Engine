import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { JournalEntryRecord } from "@/lib/types";
import { formatCompactDate } from "@/lib/utils";


export function JournalFeed({ entries }: { entries: JournalEntryRecord[] }) {
  return (
    <div className="space-y-4">
      {entries.map((entry) => (
        <Card key={`${entry.title}-${entry.timestamp}`} className={(entry.tags ?? []).includes("review-note") ? "border-cyan-300/20 bg-cyan-300/[0.04]" : undefined}>
          <CardHeader className="flex flex-col items-start justify-between gap-4 sm:flex-row">
            <div>
              <CardTitle>{entry.title}</CardTitle>
              <p className="mt-2 text-sm text-white/55">{formatCompactDate(entry.timestamp)}</p>
            </div>
            <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:justify-end">
              {(entry.tags ?? []).map((tag) => (
                <Badge key={tag} variant="neutral">
                  {tag}
                </Badge>
              ))}
            </div>
          </CardHeader>
          <CardContent className="pt-0 text-sm leading-6 text-white/72">{renderJournalBody(entry.body)}</CardContent>
        </Card>
      ))}
    </div>
  );
}

function renderJournalBody(body: string): ReactNode {
  const lines = body.split("\n");
  return lines.map((line, index) => (
    <div key={`${line}-${index}`} className={index === 0 ? "" : "mt-1"}>
      {renderLinks(line)}
    </div>
  ));
}

function renderLinks(text: string): ReactNode {
  const parts = text.split(/(https?:\/\/\S+)/g);

  return parts.map((part, index) => {
    if (/^https?:\/\/\S+$/.test(part)) {
      return (
        <a
          key={`${part}-${index}`}
          href={part}
          target="_blank"
          rel="noreferrer"
          className="break-all text-cyan-100 underline decoration-cyan-300/40 underline-offset-4"
        >
          {part}
        </a>
      );
    }

    return <span key={`${part}-${index}`}>{part}</span>;
  });
}
