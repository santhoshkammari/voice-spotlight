#!/bin/bash
cd "$(dirname "$0")"
pgrep -f "python.*run.py" > /dev/null && echo "already running" && exit 0
nohup /home/ntlpt24/main/bin/python3 run.py </dev/null >/tmp/voice-spotlight.log 2>&1 &
echo "started: $!"
