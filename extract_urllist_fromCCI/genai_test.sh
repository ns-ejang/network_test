#!/bin/bash

CSV="genai_urls.csv"

trap "echo 'Stopping...'; kill 0" SIGINT

# URL만 추출해서 배열에 저장 (zsh/bash 호환)
URLS=()
while IFS=',' read -r _ domain; do
  domain="${domain//$'\r'/}"
  [[ "$domain" == "domain" ]] && continue
  [[ -z "$domain" ]] && continue
  if [[ "$domain" != http://* && "$domain" != https://* ]]; then
    domain="https://$domain"
  fi
  URLS+=("$domain")
done < "$CSV"

while true; do
  for url in "${URLS[@]}"; do
    read -r http_code bytes <<< "$(curl -sk -o /dev/null -w '%{http_code} %{size_download}' "$url")"
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

