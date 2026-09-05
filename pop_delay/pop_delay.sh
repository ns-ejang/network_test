#!/bin/zsh
# Delay the currently connected Netskope PoP so the next GSLB cycle can fail over.
# Uses macOS dummynet (dnctl + pf). Outbound delay only, so +100 ≈ +100ms RTT.
set -euo pipefail

LOG="${NSDEBUGLOG:-/Library/Logs/Netskope/nsdebuglog.log}"
PIPE=91
ANCHOR="com.apple/ns_pop_delay"
STATE="${NSPOP_DELAY_STATE:-/tmp/ns-pop-delay.state}"
KIND="all"          # nsclient | npa | all
DRY_RUN=0
BOTH_WAYS=0
NO_WATCH=0
DELAY_MS=""
CMD=""

usage() {
  cat <<'EOF'
Usage:
  sudo pop_delay.sh 100              Apply +100ms delay to the current PoP
  sudo pop_delay.sh --delay 100
  sudo pop_delay.sh apply 100ms
  pop_delay.sh status                Show current PoP / RTT / latest GSLB logs
  sudo pop_delay.sh clear            Remove delay
  pop_delay.sh watch                 Follow GSLB logs until curPop changes

Options:
  --delay <ms>       Delay in milliseconds (suffix ms is optional)
  --kind <name>      nsclient | npa | all   (default: all)
  --both-ways        Delay in + out (adds ~2x the given value to RTT)
  --no-watch         After apply, print logs but do not follow the next cycle
  --log <path>       nsdebuglog path
  --dry-run          Parse and print actions, do not change pf/dnctl
  -h, --help

Switch threshold used for the preview:
  current >= other * 1.25  AND  current - other >= 20ms

Examples:
  sudo ./pop_delay.sh 100
  sudo ./pop_delay.sh --kind nsclient --delay 80
  sudo ./pop_delay.sh clear
EOF
}

die() { print -u2 "error: $*"; exit 1; }

need_macos() {
  [[ "$(uname -s)" == Darwin ]] || die "macOS only (dnctl/pf)"
}

parse_delay_value() {
  local raw="$1"
  raw="${raw:l}"
  raw="${raw%msec}"
  raw="${raw%ms}"
  [[ "$raw" == <-> ]] || die "invalid delay: $1 (expected e.g. 100 or 100ms)"
  (( raw > 0 )) || die "delay must be > 0"
  (( raw <= 5000 )) || die "delay $raw ms looks too high (max 5000)"
  DELAY_MS=$raw
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --delay) [[ $# -ge 2 ]] || die "--delay needs a value"; parse_delay_value "$2"; CMD=${CMD:-apply}; shift 2 ;;
    --kind) [[ $# -ge 2 ]] || die "--kind needs nsclient|npa|all"; KIND="${2:l}"; shift 2 ;;
    --both-ways) BOTH_WAYS=1; shift ;;
    --no-watch) NO_WATCH=1; shift ;;
    --log) [[ $# -ge 2 ]] || die "--log needs a path"; LOG="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    status|clear|watch|apply) CMD="$1"; shift ;;
    [0-9]*) parse_delay_value "$1"; CMD=${CMD:-apply}; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

CMD=${CMD:-status}
case "$KIND" in
  nsclient|npa|all) ;;
  *) die "--kind must be nsclient, npa, or all" ;;
esac

kinds_to_use() {
  case "$KIND" in
    nsclient) print NSClient ;;
    npa) print NPA ;;
    all) print NSClient; print NPA ;;
  esac
}

# Latest "post client rtt" block for one kind: pop ip rtt
latest_rtt_block() {
  local kind="$1"
  grep "gslb \[${kind}\]: post client rtt pop:" "$LOG" | tail -10 || true
}

latest_curpop() {
  local kind="$1"
  grep "gslb \[${kind}\].*curPop:" "$LOG" | tail -1 | sed -n 's/.*curPop:\([^[:space:]]*\).*/\1/p' || true
}

ip_to_cidr24() {
  local ip="$1"
  print "${ip%.*}.0/24"
}

# Fill globals for one kind: CURPOP, CUR_IP, CUR_RTT, BEST_POP, BEST_RTT, CIDR
load_kind() {
  local kind="$1" line pop ip rtt
  CURPOP="" CUR_IP="" CUR_RTT="" BEST_POP="" BEST_RTT="" CIDR=""
  CURPOP=$(latest_curpop "$kind")
  [[ -n "$CURPOP" ]] || return 1

  local best=999999
  while IFS= read -r line; do
    pop=$(print "$line" | sed -n 's/.*pop:\([^[:space:]]*\) ip:\([^[:space:]]*\) rtt:\([^[:space:]]*\).*/\1/p')
    ip=$(print "$line" | sed -n 's/.*pop:\([^[:space:]]*\) ip:\([^[:space:]]*\) rtt:\([^[:space:]]*\).*/\2/p')
    rtt=$(print "$line" | sed -n 's/.*pop:\([^[:space:]]*\) ip:\([^[:space:]]*\) rtt:\([^[:space:]]*\).*/\3/p')
    [[ -n "$pop" && -n "$ip" && -n "$rtt" ]] || continue
    if [[ "$pop" == "$CURPOP" ]]; then
      CUR_IP=$ip
      CUR_RTT=$rtt
    elif (( rtt < best )); then
      best=$rtt
      BEST_POP=$pop
      BEST_RTT=$rtt
    fi
  done < <(latest_rtt_block "$kind")

  [[ -n "$CUR_IP" && -n "$CUR_RTT" ]] || return 1
  CIDR=$(ip_to_cidr24 "$CUR_IP")
  return 0
}

ceil_div_pct() {
  # max(other * 1.25, other + 20) using integer math: other*5/4
  local other=$1
  local pct=$(( (other * 5 + 3) / 4 ))
  local abs=$(( other + 20 ))
  if (( pct > abs )); then print $pct; else print $abs; fi
}

preview_kind() {
  local kind="$1"
  if ! load_kind "$kind"; then
    printf "  %-9s  (no GSLB data in log)\n" "$kind"
    return 1
  fi
  local need after extra
  need=$(ceil_div_pct "$BEST_RTT")
  if [[ -n "$DELAY_MS" ]]; then
    after=$(( CUR_RTT + DELAY_MS ))
    extra=""
    if (( after >= need )); then extra="likely SWITCH to ${BEST_POP}"; else extra="below threshold (need >= ${need}ms)"; fi
    printf "  %-9s  curPop=%-8s rtt=%sms  best_other=%s/%sms  need>=%sms  after_delay=%sms  %s\n" \
      "$kind" "$CURPOP" "$CUR_RTT" "$BEST_POP" "$BEST_RTT" "$need" "$after" "$extra"
  else
    printf "  %-9s  curPop=%-8s rtt=%sms  ip=%s  cidr=%s  best_other=%s/%sms  need>=%sms\n" \
      "$kind" "$CURPOP" "$CUR_RTT" "$CUR_IP" "$CIDR" "$BEST_POP" "$BEST_RTT" "$need"
  fi
}

collect_cidrs() {
  local kind
  typeset -A seen
  CIDRS=()
  POPS=()
  for kind in $(kinds_to_use); do
    if load_kind "$kind"; then
      if [[ -z "${seen[$CIDR]:-}" ]]; then
        CIDRS+=("$CIDR")
        POPS+=("$CURPOP")
        seen[$CIDR]=1
      fi
    fi
  done
  (( ${#CIDRS[@]} > 0 )) || die "could not parse current PoP from $LOG"
}

print_gslb_cycle() {
  local kind="$1"
  print -- "----- latest ${kind} GSLB cycle -----"
  awk -v kind="$kind" '
    index($0, "gslb [" kind "] Pops fetched begin") { buf=""; on=1 }
    on && index($0, "gslb [" kind "]") { buf = buf $0 ORS }
    on && index($0, "gslb [" kind "]") && index($0, "curPop:") { on=0 }
    END {
      if (buf == "") print "(no cycle found)"
      else printf "%s", buf
    }
  ' "$LOG"
}

print_curpop_history() {
  print -- "----- curPop history (recent) -----"
  local line ts kind pop note
  while IFS= read -r line; do
    ts=$(printf '%s\n' "$line" | awk '{print $1, $2}')
    kind=$(printf '%s\n' "$line" | sed -n 's/.*gslb \[\([^]]*\)\].*/\1/p')
    pop=$(printf '%s\n' "$line" | sed -n 's/.*curPop:\([^[:space:]]*\).*/\1/p')
    if printf '%s\n' "$line" | grep -q "not modified"; then
      note="UNCHANGED"
    elif printf '%s\n' "$line" | grep -q "modified"; then
      note="CHANGED"
    else
      note="decision"
    fi
    printf "  %s  %-9s  curPop=%-8s  %s\n" "$ts" "$kind" "$pop" "$note"
  done < <(grep -E "gslb \[(NSClient|NPA)\].*curPop:" "$LOG" | tail -12) || true
}

print_gslb_logs() {
  print_curpop_history
  print_gslb_cycle NSClient
  print_gslb_cycle NPA
}

annotate_curpop_line() {
  local line="$1" kind pop note
  kind=$(printf '%s\n' "$line" | sed -n 's/.*gslb \[\([^]]*\)\].*/\1/p')
  pop=$(printf '%s\n' "$line" | sed -n 's/.*curPop:\([^[:space:]]*\).*/\1/p')
  [[ -n "$kind" && -n "$pop" ]] || return 0
  local prev="${CURPOP_SEEN[$kind]:-}"
  if [[ -z "$prev" ]]; then
    note="baseline curPop=${pop}"
  elif [[ "$prev" == "$pop" ]]; then
    note="POP UNCHANGED [${kind}] ${pop}"
  else
    note="POP CHANGED [${kind}] ${prev} -> ${pop}"
  fi
  CURPOP_SEEN[$kind]=$pop
  print -- ">>> ${note}"
}

print_status() {
  print "log: $LOG"
  if [[ -f "$STATE" ]]; then
    print "delay: active"
    cat "$STATE"
  else
    print "delay: inactive"
  fi
  print "latest GSLB summary:"
  local kind
  for kind in NSClient NPA; do
    preview_kind "$kind" || true
  done
  print_gslb_logs
}

require_root() {
  (( DRY_RUN )) && return 0
  if [[ $EUID -ne 0 ]]; then
    local -a args
    args=(--kind "$KIND" --log "$LOG")
    [[ -n "$DELAY_MS" ]] && args+=(--delay "$DELAY_MS")
    (( BOTH_WAYS )) && args+=(--both-ways)
    (( NO_WATCH )) && args+=(--no-watch)
    exec sudo -- "$0" "${args[@]}" "$CMD"
  fi
}

write_state() {
  {
    print "delay_ms=${DELAY_MS}"
    print "pipe=${PIPE}"
    print "anchor=${ANCHOR}"
    print "kind=${KIND}"
    print "both_ways=${BOTH_WAYS}"
    print "cidrs=${(j:,:)CIDRS}"
    print "pops=${(j:,:)POPS}"
    print "pf_token=${PF_TOKEN:-}"
    print "applied_at=$(date '+%Y-%m-%d %H:%M:%S')"
  } > "$STATE"
}

apply_delay() {
  [[ -n "$DELAY_MS" ]] || die "specify a delay, e.g. $0 100"
  [[ -f "$LOG" ]] || die "log not found: $LOG"
  collect_cidrs

  print "Applying +${DELAY_MS}ms outbound delay to current PoP"
  local kind
  for kind in $(kinds_to_use); do
    preview_kind "$kind" || true
  done
  print "targets: ${CIDRS[*]}  (pops: ${POPS[*]})"
  if (( BOTH_WAYS )); then
    print "note: --both-ways is on, so observed RTT increase will be ~$(( DELAY_MS * 2 ))ms"
  fi

  print ""
  print "current GSLB logs:"
  print_gslb_logs

  (( DRY_RUN )) && { print "dry-run: not changing pf/dnctl"; return 0; }

  require_root
  local pf_out
  pf_out=$(pfctl -E 2>&1) || true
  PF_TOKEN=$(print "$pf_out" | awk '/Token/ {print $NF; exit}')

  dnctl pipe "$PIPE" config delay "$DELAY_MS" || die "dnctl pipe failed"

  local rules=() cidr
  for cidr in "${CIDRS[@]}"; do
    rules+=("dummynet out quick inet from any to ${cidr} pipe ${PIPE}")
    if (( BOTH_WAYS )); then
      rules+=("dummynet in quick inet from ${cidr} to any pipe ${PIPE}")
    fi
  done
  print -l -- "${rules[@]}" | pfctl -a "$ANCHOR" -f - || die "pfctl anchor load failed"

  write_state
  print "applied. Next GSLB cycle is ~14 minutes."
  print "Clear with: sudo $0 clear"
  if (( NO_WATCH )); then
    print "skipping watch (--no-watch). Follow later with: $0 watch"
    return 0
  fi
  print ""
  watch_cycle
}

clear_delay() {
  require_root
  (( DRY_RUN )) && { print "dry-run: would clear pipe $PIPE and anchor $ANCHOR"; return 0; }

  local token=""
  if [[ -f "$STATE" ]]; then
    token=$(awk -F= '/^pf_token=/ {print $2}' "$STATE")
  fi

  pfctl -a "$ANCHOR" -F all >/dev/null 2>&1 || true
  dnctl pipe delete "$PIPE" >/dev/null 2>&1 || true

  if [[ -n "$token" ]]; then
    pfctl -X "$token" >/dev/null 2>&1 || true
  fi
  rm -f "$STATE"
  print "cleared PoP delay"
}

watch_cycle() {
  [[ -f "$LOG" ]] || die "log not found: $LOG"
  typeset -gA CURPOP_SEEN
  CURPOP_SEEN[NSClient]=$(latest_curpop NSClient)
  CURPOP_SEEN[NPA]=$(latest_curpop NPA)
  if [[ "$CMD" == watch ]]; then
    print_gslb_logs
  fi
  print -- "----- watching GSLB logs (Ctrl-C to stop) -----"
  print "baseline  NSClient=${CURPOP_SEEN[NSClient]:-?}  NPA=${CURPOP_SEEN[NPA]:-?}"
  print "waiting for the next cycle; look for >>> POP CHANGED"

  local line
  while IFS= read -r line; do
    printf '%s\n' "$line"
    if [[ "$line" == *curPop:* ]]; then
      annotate_curpop_line "$line"
    fi
  done < <(tail -n 0 -f "$LOG" | grep --line-buffered -E 'gslb \[(NSClient|NPA)\]')
}

need_macos
[[ -f "$LOG" ]] || die "log not found: $LOG"

case "$CMD" in
  status) print_status ;;
  apply) apply_delay ;;
  clear) clear_delay ;;
  watch) watch_cycle ;;
  *) die "unknown command: $CMD" ;;
esac
