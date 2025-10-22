# Transcription Editor (Local macOS Speech)

A PyQt app for recording audio, transcribing locally using Apple's on-device Speech models, editing by re-recording selected transcript ranges (like Descript), and exporting final audio.

## Requirements
- macOS (Apple Silicon or Intel) with on-device speech model for your language installed (System Settings > Keyboard > Dictation > Downloaded languages)
- Python 3.10+
- `pip install -r requirements.txt`
- First run will prompt for microphone and speech recognition permissions

If `sounddevice` fails, you may need PortAudio:
- `brew install portaudio`

If you prefer using libsndfile (optional):
- `brew install libsndfile`
- uncomment `soundfile` in `requirements.txt`

## Run
```bash
python3 app/main.py
```

## Notes
- Transcription uses Apple's Speech framework via PyObjC with `requiresOnDeviceRecognition` enabled. Ensure your language is available offline.
- After each re-record, the app re-generates the full transcript to keep word timings aligned.
- Export produces a 16‑bit PCM WAV file.
