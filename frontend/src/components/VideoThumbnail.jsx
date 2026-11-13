import { useState } from 'react'
import { formatDuration } from '../utils/format.js'

/** 16:9 thumbnail with duration overlay; falls back to a placeholder if missing or broken. */
export default function VideoThumbnail({ src, alt, durationSeconds, className = '' }) {
  const [broken, setBroken] = useState(false)
  return (
    <div className={`relative aspect-video overflow-hidden rounded-lg bg-slate-800 ${className}`}>
      {src && !broken ? (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setBroken(true)}
          className="h-full w-full object-cover"
        />
      ) : (
        <div className="grid h-full w-full place-items-center text-2xl text-slate-600" aria-hidden="true">▶</div>
      )}
      {durationSeconds != null && (
        <span className="absolute right-1.5 bottom-1.5 rounded bg-black/80 px-1.5 py-0.5 text-xs font-medium tabular-nums">
          {formatDuration(durationSeconds)}
        </span>
      )}
    </div>
  )
}
