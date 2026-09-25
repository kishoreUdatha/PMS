"""Line-oriented TTS worker for the demo recorder.

Loads a Piper voice once, then for each JSON line {"text", "out"} on stdin
writes a WAV and answers {"out", "seconds"} on stdout. Keeping the model
loaded is the point: a fresh `piper` process per caption costs a second or
two of dead video every time.
"""
import json
import sys
import wave

from piper import PiperVoice

voice = PiperVoice.load(sys.argv[1])
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    with wave.open(req["out"], "wb") as wf:
        voice.synthesize_wav(req["text"], wf)
    with wave.open(req["out"], "rb") as wf:
        seconds = wf.getnframes() / wf.getframerate()
    print(json.dumps({"out": req["out"], "seconds": seconds}), flush=True)
