#!/bin/bash
# Transcription script - output is stored on the message row and sent as a WhatsApp reply
# Future: integrate with speech-to-text service

if [ -z "$1" ]; then
    echo "Usage: $0 <audio_file>" >&2
    exit 1
fi

duration=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null)
printf "Got audio: %.0f seconds\n" "$duration"
