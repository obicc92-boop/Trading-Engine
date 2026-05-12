import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import type { SignalRecord } from "@/lib/types";
import { formatCompactDate } from "@/lib/utils";


function actionVariant(action: SignalRecord["action"]): "positive" | "negative" | "neutral" {
  if (action === "BUY") {
    return "positive";
  }
  if (action === "SELL") {
    return "negative";
  }
  return "neutral";
}

export function SignalsTable({ signals }: { signals: SignalRecord[] }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-white/10 bg-white/[0.03]">
      <Table className="min-w-[680px]">
        <TableHead>
          <TableRow>
            <TableHeaderCell>Pair</TableHeaderCell>
            <TableHeaderCell>Timeframe</TableHeaderCell>
            <TableHeaderCell>Action</TableHeaderCell>
            <TableHeaderCell>Confidence</TableHeaderCell>
            <TableHeaderCell>Intent</TableHeaderCell>
            <TableHeaderCell>Timestamp</TableHeaderCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {signals.map((signal) => (
            <TableRow key={`${signal.symbol}-${signal.interval}-${signal.timestamp}`}>
              <TableCell className="font-medium text-white">{signal.symbol}</TableCell>
              <TableCell>{signal.interval}</TableCell>
              <TableCell>
                <Badge variant={actionVariant(signal.action)}>{signal.action}</Badge>
              </TableCell>
              <TableCell>{signal.confidence}%</TableCell>
              <TableCell>{signal.execution_intent}</TableCell>
              <TableCell>{formatCompactDate(signal.timestamp)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
