interface SegmentedControlOption<Value extends string> {
  value: Value
  label: string
}

interface SegmentedControlProps<Value extends string> {
  /** Accessible name for the group (spec 12.4: semantic markup + aria-*). */
  label: string
  options: SegmentedControlOption<Value>[]
  value: Value
  onChange: (value: Value) => void
}

/** A row of mutually-exclusive buttons acting as one radio group -- the
 * source/mode toggles in AddSongForm and RecordingCapture, and the
 * top-level Analyze/Progress nav in App.tsx, all needed this exact pattern
 * (spec 12.1 DRY: a rule repeated a third time gets pulled out). */
export function SegmentedControl<Value extends string>({
  label,
  options,
  value,
  onChange,
}: SegmentedControlProps<Value>) {
  return (
    <div role="radiogroup" aria-label={label} className="flex gap-2">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          onClick={() => onChange(option.value)}
          className={`focus-visible:outline-ink-950 flex-1 rounded border px-3 py-2 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ${
            option.value === value
              ? 'bg-ink-950 border-ink-950 text-ink-0'
              : 'border-ink-300 text-ink-700 hover:bg-ink-100'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
