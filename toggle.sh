#!/bin/bash
cd "$(dirname "$0")"
if pgrep -f "python.*run.py" > /dev/null; then
    pkill -9 -f "python.*run.py"
    echo "stopped"
else
    nohup /home/ntlpt24/main/bin/python3 run.py </dev/null >/tmp/voice-spotlight.log 2>&1 &
    echo "started: $!"
fi
