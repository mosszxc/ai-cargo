#!/usr/bin/env bash
# Wrapper for faster-whisper transcription using InvokeAI venv
# Usage: whisper-transcribe.sh <audio_file>
# Outputs plain text transcript to stdout

set -euo pipefail

VENV="/home/dev-moss/InvokeAI/apps/api/decision-engine-service/.venv"
MODEL="large-v3"
DEVICE="cuda"
COMPUTE_TYPE="float16"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <audio_file>" >&2
    exit 1
fi

INPUT_FILE="$1"

if [ ! -f "$INPUT_FILE" ]; then
    echo "File not found: $INPUT_FILE" >&2
    exit 1
fi

"${VENV}/bin/python" -c "
import sys
from faster_whisper import WhisperModel

model = WhisperModel('${MODEL}', device='${DEVICE}', compute_type='${COMPUTE_TYPE}')
segments, info = model.transcribe(
    sys.argv[1],
    beam_size=5,
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500),
)
text = ' '.join(seg.text.strip() for seg in segments)
print(text)
" "$INPUT_FILE"
