#!/bin/bash
# Record one round of demos of every mode (GPU), then mark the round DONE.
# Usage: tools/demo-round.sh            (one round)
#        tools/demo-round.sh loop 600   (a round every 600 s until stopped)
cd "$(dirname "$0")/.." || exit 1
round() {
  ts=$(date +%H%M)
  dir="demos/raw/$ts"; mkdir -p "$dir"
  # record a frozen build (frozen/, :5197) so agents editing files mid-round cannot reload the page
  tools/publish-stable.sh frozen > "$dir/build.log" 2>&1 || echo "build failed, recording the last stable build" >> "$dir/build.log"
  export CAPTURE_BASE=http://127.0.0.1:5197/
  FOCUS="spine.ctx.world.nodes.findIndex(n=>n.mine&&n.title==='Dune')"
  for m in burst orbit tunnel; do
    node tools/capture.mjs video "$m" "$dir/$m.gif" 1280 800 \
      "[{\"wait\":2600},{\"move\":[300,300]},{\"move\":[900,500]},{\"eval\":\"spine.open($FOCUS)\"},{\"wait\":3200},{\"eval\":\"spine.open(spine.ctx.world.nodes[$FOCUS].next[0])\"},{\"wait\":3000},{\"key\":\"Escape\"},{\"wait\":2600}]" 13 12 > "$dir/$m.log" 2>&1
    uv run --no-project --with pillow python tools/shrink-gif.py "$dir/$m.gif" "$dir/$m.webp" >> "$dir/$m.log" 2>&1
    node tools/capture.mjs shot "$m" "$dir/$m.png" 1440 900 '[{"wait":3500}]' >> "$dir/$m.log" 2>&1
  done
  # the signature move: one mode morphing into the next
  node tools/capture.mjs video orbit "$dir/morph.gif" 1280 800 \
    '[{"wait":3000},{"record":true},{"key":"3"},{"wait":3200},{"key":"1"},{"wait":3200},{"key":"2"},{"wait":3200}]' 10 12 > "$dir/morph.log" 2>&1
  uv run --no-project --with pillow python tools/shrink-gif.py "$dir/morph.gif" "$dir/morph.webp" >> "$dir/morph.log" 2>&1
  echo "round $ts done $(date +%T)" > "$dir/DONE"
  echo "ROUND $dir"
}
if [ "$1" = "loop" ]; then
  while true; do round; sleep "${2:-600}"; done
else
  round
fi
