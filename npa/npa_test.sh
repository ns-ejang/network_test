#!/bin/bash

# Ctrl+C 들어오면 모든 백그라운드 job 종료
trap "echo 'Stopping...'; kill 0" SIGINT

probe() {
  local host=$1 port=$2 svc=$3
  local result
  if nc -z -w 3 "$host" "$port" >/dev/null 2>&1; then
    result="succeeded!"
  else
    result="failed!"
  fi
  printf "Connection to %-28s  port %-5s  [tcp/%-5s]  %s\n" \
    "$host" "$port" "$svc" "$result"
}

while true; do
  probe 192.168.0.168 22 ssh
  sleep 5
done &

while true; do
  probe 192.168.0.1 443 https
  sleep 1
done &

while true; do
  probe the-internet.herokuapp.com 443 https
  sleep 1
done &

while true; do
  probe pim.cyberlogitec.com 443 https
  sleep 1
done &

while true; do
  probe gitlab.cyberlogitec.com 443 https
  sleep 1
done &

wait

