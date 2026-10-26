#!/usr/bin/env bash
# Where voice sessions spent their time, from the logs, to investigate latency and lip sync. It
# changes nothing. Times are UTC, as in the pod logs.
#   - the voice agent: per session the avatar and face; per turn the delays (end of speech to first
#     audio, transcription, the language model's first token, the speech's first audio, the
#     avatar's playback delay); noise dropped; avatar start failures;
#   - OpenSpline: its render time per chunk of video (0.96 s of speech each), the slow ones, and
#     how long each reset to the portrait's pose took. It reads OpenSpline's "[generate] <step>:
#     <seconds>" lines, a chunk ending at "color correction"; if that format changes it reports
#     no chunk;
#   - LiveKit: how each browser reached it (directly, or relayed through TURN).
#
#   NS=<project> scripts/voice-timings.sh              the last 30 minutes
#   SINCE=2h NS=<project> scripts/voice-timings.sh
set -euo pipefail
NS="${NS:-$(oc project -q)}"
SINCE="${SINCE:-30m}"
short() { sed -E 's/(session[- =]?)([0-9a-f]{8})[0-9a-f]{24}/\1\2/g'; }

echo "=== voice agent, last $SINCE (UTC) ==="
oc logs deploy/voice-agent -n "$NS" --since="$SINCE" 2>/dev/null | grep -v '^{' \
  | grep -E 'joined room|avatar provider|timings|latency:|noise transcript|did not start|failed to start|speech not done' \
  | grep -v remote_session | short || echo "  (nothing logged)"

if oc get deploy/openspline -n "$NS" >/dev/null 2>&1; then
  echo
  echo "=== OpenSpline, last $SINCE (UTC) ==="
  oc logs deploy/openspline -n "$NS" -c openspline --timestamps --since="$SINCE" 2>/dev/null | awk '
    /\[generate\]/ { t = $NF; sub(/s$/, "", t); chunk += t; if ($0 ~ /color correction/) { n++; sum += chunk; if (chunk > max) max = chunk; if (chunk > 0.5) slow = slow sprintf("  slow chunk at %s: %.2fs\n", substr($1, 12, 12), chunk); chunk = 0 } }
    /\[pose\]/ { sub(/^[^ ]+ /, substr($1, 12, 12) " "); print "  " $0 }
    END {
      if (n) printf "  %d chunks rendered: %.3fs on average, %.3fs at most, for 0.96s of video each (real time needs under 0.96s)\n", n, sum / n, max
      else print "  no chunk rendered (no OpenSpline session in this window)"
      printf "%s", slow
    }'
  oc logs deploy/openspline -n "$NS" -c openspline 2>/dev/null | grep -E 'publish options|pose reset' | sed 's/^/  start: /' | head -4 || true
fi

echo
echo "=== how browsers reached LiveKit, last $SINCE (UTC) ==="
oc logs deploy/livekit -n "$NS" --since="$SINCE" 2>/dev/null | grep 'participant active' | grep '"participant": "user-' \
  | sed -E 's/^([0-9T:-]+)\.[0-9]+Z.*"room": "([^"]+)".*"connectionType": "([a-z]+)".*/  \1  \3  \2/' | short || echo "  (no browser session)"
