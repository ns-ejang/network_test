#!/bin/bash

CSV="month.csv"

trap "echo 'Stopping...'; kill 0" SIGINT

# 3번째 칼럼이 url인 행만, 4번째 칼럼을 URL로 사용 (zsh 호환)
URLS=()
while IFS=',' read -r _ _ type url _; do
  [[ "$type" == "url" ]] || continue
  URLS+=("$url")
done < "$CSV"

while true; do
  for url in "${URLS[@]}"; do
    read -r http_code bytes <<< "$(curl --connect-timeout 5 -m 10 -sk -o /dev/null -w '%{http_code} %{size_download}' "$url")"
    case "$http_code" in
      200) phrase="OK" ;;
      201) phrase="Created" ;;
      204) phrase="No Content" ;;
      301|302|303|307|308) phrase="Redirect" ;;
      400) phrase="Bad Request" ;;
      401) phrase="Unauthorized" ;;
      403) phrase="Forbidden" ;;
      404) phrase="Not Found" ;;
      500) phrase="Internal Server Error" ;;
      502) phrase="Bad Gateway" ;;
      503) phrase="Service Unavailable" ;;
      000) phrase="Connection Failed" ;;
      *) phrase="" ;;
    esac
    if [[ -n "$phrase" ]]; then
      echo "[ACCESS] $url   --- $http_code $phrase"
    else
      echo "[ACCESS] $url   --- $http_code"
    fi
    echo "[TRAFFIC] $bytes"
    sleep 1
  done
done
