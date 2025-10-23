# Transcription Editor (Local macOS Speech)

A PyQt app for recording audio, transcribing locally using Apple's on-device Speech models, editing by re-recording selected transcript ranges (like Descript), and exporting final audio.

## Requirements
- macOS (Apple Silicon or Intel) with on-device speech model for your language installed (System Settings > Keyboard > Dictation > Downloaded languages)
- Python 3.10+
- First run will prompt for microphone and speech recognition permissions

If `sounddevice` fails, you may need PortAudio:
- `brew install portaudio`

If you prefer using libsndfile (optional):
- `brew install libsndfile`
- uncomment `soundfile` in `requirements.txt`

## Setup

### Option 1: Using Virtual Environment (Recommended)

This approach keeps all dependencies isolated from your system Python installation.

```bash
# Run the setup script (creates venv and installs dependencies)
./setup_venv.sh

# Activate the virtual environment
source venv/bin/activate

# Run the app
python app/main.py

# When done, deactivate the virtual environment
deactivate
```

### Option 2: Direct Installation

```bash
pip install -r requirements.txt
```

## Run
```bash
# If using virtual environment, make sure it's activated first
# source venv/bin/activate

python app/main.py
```

## Notes
- Transcription uses Apple's Speech framework via PyObjC with `requiresOnDeviceRecognition` enabled. Ensure your language is available offline.
- After each re-record, the app re-generates the full transcript to keep word timings aligned.
- Export produces a 16‑bit PCM WAV file.
