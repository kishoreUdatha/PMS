interface PlaceholderProps {
  title: string
  note?: string
}

// Used for screens not yet implemented so navigation works end-to-end.
export default function Placeholder({ title, note }: PlaceholderProps) {
  return (
    <div className="space-y-4">
      <h1 className="text-display text-ink">{title}</h1>
      <div className="flex h-64 items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white text-slate-400">
        {note ?? `${title} — screen to be implemented from the mockup.`}
      </div>
    </div>
  )
}
