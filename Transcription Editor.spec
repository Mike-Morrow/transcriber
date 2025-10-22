# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/Users/mmorrow@hex.tech/Documents/Transcription Editor/app/main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Transcription Editor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Transcription Editor',
)
app = BUNDLE(
    coll,
    name='Transcription Editor.app',
    icon=None,
    bundle_identifier='com.example.transcriptioneditor',
    info_plist={
        'CFBundleShortVersionString': '0.1.0',
        'CFBundleVersion': '0.1.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'NSMicrophoneUsageDescription': 'Microphone access is required to record audio.',
        'NSSpeechRecognitionUsageDescription': 'Speech recognition is used to transcribe audio locally on-device.'
    }
)
