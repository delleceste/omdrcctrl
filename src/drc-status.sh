#!/bin/bash
# Print the active brutefir config name (e.g. 120.blue+0dB), or 'off'.
# Exits 1 and prints 'inconsistent' if multiple different configs are running.

configs=$(ps -C brutefir -o args= 2>/dev/null \
    | sed -n 's|.*brutefir-\([^ ]*\)\.conf.*|\1|p' \
    | sort -u)

if [ -z "$configs" ]; then
    echo off
    exit 0
fi

n=$(printf '%s\n' "$configs" | wc -l)
if [ "$n" -eq 1 ]; then
    echo "$configs"
else
    echo "inconsistent"
    exit 1
fi
