create extension if not exists pgcrypto;

create table if not exists public.signals (
    id uuid primary key default gen_random_uuid(),
    symbol text not null,
    interval text not null,
    action text not null check (action in ('BUY', 'SELL', 'HOLD')),
    confidence integer not null check (confidence between 0 and 100),
    eligible boolean not null default false,
    execution_intent text not null default 'NO_ACTION',
    market text not null default 'spot',
    entry numeric,
    stop_loss numeric,
    take_profit numeric,
    position_size numeric,
    reason text not null,
    rejection_reasons jsonb not null default '[]'::jsonb,
    warnings jsonb not null default '[]'::jsonb,
    indicators jsonb not null default '{}'::jsonb,
    timestamp timestamptz not null default timezone('utc', now())
);

create table if not exists public.trades (
    id uuid primary key default gen_random_uuid(),
    symbol text not null,
    interval text not null,
    action text not null check (action in ('BUY', 'SELL', 'HOLD')),
    execution_mode text not null check (execution_mode in ('paper', 'manual', 'live')),
    status text not null default 'OPEN',
    market text not null default 'spot',
    entry numeric not null,
    stop_loss numeric not null,
    take_profit numeric not null,
    position_size numeric not null,
    confidence integer not null check (confidence between 0 and 100),
    risk_reward_ratio numeric not null,
    notional_value numeric not null,
    exit_price numeric,
    pnl numeric,
    pnl_pct numeric,
    notes text not null default '',
    metadata jsonb not null default '{}'::jsonb,
    opened_at timestamptz not null default timezone('utc', now()),
    closed_at timestamptz
);

alter table public.trades drop constraint if exists trades_status_check;
alter table public.trades
    add constraint trades_status_check
    check (status in ('PENDING_APPROVAL', 'PENDING_CONFIRMATION', 'ENTRY_SUBMITTED', 'OPEN', 'EXIT_SUBMITTED', 'CLOSED', 'CANCELLED'));

create table if not exists public.journal_entries (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    body text not null,
    symbol text,
    related_trade_id uuid references public.trades(id) on delete set null,
    tags jsonb not null default '[]'::jsonb,
    timestamp timestamptz not null default timezone('utc', now())
);

create table if not exists public.performance_logs (
    id uuid primary key default gen_random_uuid(),
    execution_mode text not null check (execution_mode in ('paper', 'manual', 'live')),
    total_equity numeric not null,
    available_cash numeric not null,
    realized_pnl_today numeric not null,
    total_realized_pnl numeric not null,
    unrealized_pnl numeric not null,
    open_positions integer not null default 0,
    win_rate numeric not null default 0,
    timestamp timestamptz not null default timezone('utc', now())
);

create index if not exists idx_signals_timestamp on public.signals (timestamp desc);
create index if not exists idx_signals_symbol_interval on public.signals (symbol, interval);
create index if not exists idx_trades_status_mode on public.trades (status, execution_mode);
create index if not exists idx_trades_symbol_interval on public.trades (symbol, interval);
create index if not exists idx_journal_timestamp on public.journal_entries (timestamp desc);
create index if not exists idx_performance_timestamp on public.performance_logs (timestamp desc);
