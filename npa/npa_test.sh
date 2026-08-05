#!/bin/bash

# Ctrl+C 들어오면 모든 백그라운드 job 종료
trap "echo 'Stopping...'; kill 0" SIGINT

while true; do
  echo "[SSH-22]" $(nc -z -w 3 192.168.0.168 22 && echo "open" || echo "closed")
  sleep 5
done &

while true; do
  echo "[ROUTER]"
  bytes=$(curl -sk -o /dev/null -w '%{size_download}' https://192.168.0.1)
  echo "[TRAFFIC] $bytes"
  sleep 1
done &

while true; do
  echo "[VNC-5900]" $(nc -z -w 3 192.168.0.133 5900 && echo "open" || echo "closed")
  sleep 5
done &

while true; do
  echo "[UPLOAD-TEST]"
  bytes=$(curl -sk -o /dev/null -w '%{size_download}' https://the-internet.herokuapp.com/upload)
  echo "[TRAFFIC] $bytes"
  sleep 1
done &

wait

