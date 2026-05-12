export default function Loading() {
  return (
    <div className="space-y-6">
      <section className="rounded-[28px] border border-white/10 bg-white/[0.03] p-6">
        <div className="h-4 w-28 animate-pulse rounded bg-white/[0.05]" />
        <div className="mt-4 h-10 w-3/4 animate-pulse rounded bg-white/[0.08]" />
        <div className="mt-4 h-4 w-2/3 animate-pulse rounded bg-white/[0.05]" />
        <div className="mt-2 h-4 w-1/2 animate-pulse rounded bg-white/[0.05]" />
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index} className="rounded-[28px] border border-white/10 bg-white/[0.03] p-5">
            <div className="h-4 w-24 animate-pulse rounded bg-white/[0.05]" />
            <div className="mt-5 h-9 w-32 animate-pulse rounded bg-white/[0.08]" />
            <div className="mt-5 h-3 w-full animate-pulse rounded bg-white/[0.05]" />
            <div className="mt-2 h-3 w-5/6 animate-pulse rounded bg-white/[0.05]" />
          </div>
        ))}
      </section>

      <section className="grid gap-6 xl:grid-cols-2">
        {Array.from({ length: 2 }, (_, index) => (
          <div key={index} className="rounded-[28px] border border-white/10 bg-white/[0.03] p-6">
            <div className="h-4 w-28 animate-pulse rounded bg-white/[0.05]" />
            <div className="mt-4 h-7 w-56 animate-pulse rounded bg-white/[0.08]" />
            <div className="mt-8 h-72 animate-pulse rounded-[22px] bg-white/[0.04]" />
          </div>
        ))}
      </section>
    </div>
  );
}
