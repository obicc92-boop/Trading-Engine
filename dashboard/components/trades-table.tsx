import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import type { TradeRecord } from "@/lib/types";
import { formatCompactDate, formatCurrency } from "@/lib/utils";


export function TradesTable({ trades }: { trades: TradeRecord[] }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-white/10 bg-white/[0.03]">
      <Table className="min-w-[640px]">
        <TableHead>
          <TableRow>
            <TableHeaderCell>Pair</TableHeaderCell>
            <TableHeaderCell>Status</TableHeaderCell>
            <TableHeaderCell>Entry</TableHeaderCell>
            <TableHeaderCell>Exit</TableHeaderCell>
            <TableHeaderCell>PnL</TableHeaderCell>
            <TableHeaderCell>Opened</TableHeaderCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {trades.map((trade) => (
            <TableRow key={`${trade.symbol}-${trade.opened_at}-${trade.id ?? "row"}`}>
              <TableCell className="font-medium text-white">{trade.symbol}</TableCell>
              <TableCell>
                <Badge variant={trade.status === "OPEN" ? "accent" : (trade.pnl ?? 0) >= 0 ? "positive" : "negative"}>
                  {trade.status}
                </Badge>
              </TableCell>
              <TableCell>{formatCurrency(trade.entry)}</TableCell>
              <TableCell>{trade.exit_price ? formatCurrency(trade.exit_price) : "Open"}</TableCell>
              <TableCell>
                {trade.pnl == null ? "Unrealized" : <span className={(trade.pnl ?? 0) >= 0 ? "text-emerald-200" : "text-rose-200"}>{formatCurrency(trade.pnl)}</span>}
              </TableCell>
              <TableCell>{formatCompactDate(trade.opened_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
