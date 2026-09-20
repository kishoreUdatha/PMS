import { useCallback, useEffect, useRef, useState } from 'react'
import Select from './Select'
import { Camera, Loader2, RefreshCw, X, Check, VideoOff } from 'lucide-react'

/** Take a photo with the desk's webcam.
 *
 * Check-in needs an image of the guest and of their ID, and at a front desk
 * the camera is the scanner — nobody is going to find a file on disk while a
 * guest stands waiting. The captured frame is handed back as a `File`, the
 * same shape the file picker produces, so the upload path is unchanged.
 *
 * Two things here are not incidental:
 *
 * **The preview is mirrored, the capture is not.** People expect a mirror when
 * they see themselves, but the canvas draws the raw frame — so a passport held
 * up to the lens is stored with its text the right way round rather than
 * reversed.
 *
 * **The stream is always stopped.** A webcam left running keeps its light on
 * and holds the device against other software; the track is stopped when the
 * dialog closes, when the component unmounts, and before a device switch.
 */
export default function CameraCapture({
  open, title, onCancel, onCapture, mirror = true,
}: {
  open: boolean
  title: string
  onCancel: () => void
  onCapture: (file: File) => void
  /** Off for documents, where a mirrored preview makes the text unreadable. */
  mirror?: boolean
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const [deviceId, setDeviceId] = useState<string>('')
  const [shot, setShot] = useState<{ url: string; file: File } | null>(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
  }, [])

  const start = useCallback(async (id?: string) => {
    setError('')
    setStarting(true)
    stop()
    try {
      // getUserMedia only exists in a secure context. Served over plain HTTP
      // from anything but localhost the API is simply absent, and saying so
      // beats a generic failure.
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error(
          window.isSecureContext
            ? 'This browser has no camera support.'
            : 'The camera needs HTTPS. Open the PMS over https:// or on localhost.',
        )
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        video: id
          ? { deviceId: { exact: id } }
          : { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play().catch(() => {})
      }
      // Labels are blank until permission is granted, so the device list is
      // only worth reading after the stream is up.
      const all = await navigator.mediaDevices.enumerateDevices()
      setDevices(all.filter((d) => d.kind === 'videoinput'))
      setDeviceId(id ?? stream.getVideoTracks()[0]?.getSettings().deviceId ?? '')
    } catch (e) {
      const err = e as Error
      setError(
        err.name === 'NotAllowedError'
          ? 'Camera permission was refused. Allow it from the browser address bar and try again.'
          : err.name === 'NotFoundError'
            ? 'No camera is connected to this machine.'
            : err.name === 'NotReadableError'
              ? 'The camera is already in use by another application.'
              : err.message || 'The camera could not be started.',
      )
    } finally { setStarting(false) }
  }, [stop])

  useEffect(() => {
    if (open) void start()
    return stop
  }, [open, start, stop])

  // Revoke the preview URL rather than leaking one per retake.
  useEffect(() => () => { if (shot) URL.revokeObjectURL(shot.url) }, [shot])

  if (!open) return null

  function take() {
    const video = videoRef.current
    if (!video || !video.videoWidth) return
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d')?.drawImage(video, 0, 0)
    canvas.toBlob((blob) => {
      if (!blob) { setError('The frame could not be captured.'); return }
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
      setShot({
        url: URL.createObjectURL(blob),
        file: new File([blob], `capture-${stamp}.jpg`, { type: 'image/jpeg' }),
      })
      stop()   // freeze on the still; nothing to keep the camera on for
    }, 'image/jpeg', 0.92)
  }

  function retake() {
    if (shot) URL.revokeObjectURL(shot.url)
    setShot(null)
    void start(deviceId || undefined)
  }

  function close() {
    if (shot) URL.revokeObjectURL(shot.url)
    setShot(null)
    stop()
    onCancel()
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/60 p-4"
      role="dialog" aria-modal="true" aria-label={title}>
      <div className="w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          <button onClick={close} aria-label="Close"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
            <X size={16} />
          </button>
        </div>

        <div className="relative aspect-video bg-slate-900">
          {shot ? (
            <img src={shot.url} alt="Captured" className="h-full w-full object-contain" />
          ) : (
            <video ref={videoRef} playsInline muted
              className={`h-full w-full object-cover ${mirror ? 'scale-x-[-1]' : ''}`} />
          )}
          {starting && (
            <div className="absolute inset-0 grid place-items-center text-white/80">
              <Loader2 size={22} className="animate-spin" />
            </div>
          )}
          {error && !starting && (
            <div className="absolute inset-0 grid place-items-center gap-2 p-6 text-center">
              <VideoOff size={22} className="mx-auto text-white/50" />
              <p className="text-sm text-white/80">{error}</p>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
          {/* Only worth showing when there is a choice to make. */}
          {!shot && devices.length > 1 && (
            <Select value={deviceId} onChange={(e) => start(e.target.value)}
              aria-label="Camera"
              className="rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-600">
              {devices.map((d, i) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || `Camera ${i + 1}`}
                </option>
              ))}
            </Select>
          )}
          <div className="ml-auto flex items-center gap-2">
            {shot ? (
              <>
                <button onClick={retake}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                  <RefreshCw size={14} /> Retake
                </button>
                <button onClick={() => { onCapture(shot.file); close() }}
                  className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-dark">
                  <Check size={14} /> Use photo
                </button>
              </>
            ) : (
              <>
                {error && (
                  <button onClick={() => start(deviceId || undefined)}
                    className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                    Try again
                  </button>
                )}
                <button onClick={take} disabled={!!error || starting}
                  className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                  <Camera size={14} /> Capture
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
