"""Position bookkeeping — what each sale books against the trade that bought the coins.

The rule decides *whether* to sell; this decides what a sale was worth. Fees on both legs
are attributed here, and so is the arithmetic of lot steps: exchanges sell in whole steps,
so a final exit can leave a sliver behind, and the books have to keep matching the cash
when it does. Pure functions, no I/O.
"""

from __future__ import annotations

from .models import Position


def realise(position: Position, qty: float, price: float, fee: float) -> float:
    """Book a partial or full exit against the position and return the realised P&L.

    Fees on both legs are attributed here so that `realized_pnl` is what actually landed in
    the account, not a gross number that flatters the record.
    """
    share = (qty / position.initial_qty) if position.initial_qty else 0.0
    entry_fee_share = position.entry_fee * share
    gross = (price - position.entry_price) * qty
    net = gross - fee - entry_fee_share
    position.realized_pnl += net
    position.fees_paid += fee
    position.qty = max(0.0, position.qty - qty)
    return net


def write_off(position: Position) -> float:
    """Charge whatever a final exit could not sell to the trade, and close its books.

    Exchanges sell in whole lot steps, so a final exit floors its quantity and leaves a
    sliver of coin behind. `realise` charges only for what actually left, so without this
    the booked result would sit above the cash in the account by that sliver's cost — trade
    after trade, until a drawdown measured against it described money that was never lost.
    The sliver itself stays in the account, and the next final exit in the same coin sells
    it (`book_swept`).
    """
    if position.qty <= 0:
        return 0.0
    share = (position.qty / position.initial_qty) if position.initial_qty else 0.0
    cost = position.entry_price * position.qty + position.entry_fee * share
    position.realized_pnl -= cost
    position.qty = 0.0
    return -cost


def book_swept(position: Position, qty: float, price: float, fee: float) -> float:
    """Book dust left by earlier trades, sold alongside this trade's final exit.

    Its cost was charged to the trade that left it behind (`write_off`), so everything it
    fetches is result.
    """
    net = qty * price - fee
    position.realized_pnl += net
    position.fees_paid += fee
    return net
