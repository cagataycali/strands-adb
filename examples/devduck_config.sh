#!/usr/bin/env bash
# Use strands-adb inside devduck
export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell,file_read"
devduck "check my android phone's battery and current app"
