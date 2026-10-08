#!/bin/bash
# fetch_snapshots.sh <tasks.jsonl> <out_dir> [kaggle cli]
# Downloads each task's snapshot, keeps only its Python files (no .git) in <out_dir>/<instance_id>/ and deletes the
# archive: the full snapshots of the 129 dev tasks are about 19 GB, their Python files a few hundred MB.
set -u
tasks=$1; out=$2; kaggle=${3:-kaggle}
mkdir -p "$out/.dl"
for id in $(python3 -c "import json,sys; [print(json.loads(l)['instance_id']) for l in open(sys.argv[1])]" "$tasks"); do
  [ -d "$out/$id" ] && continue
  "$kaggle" competitions download gemma-4-developer-agent -f "snapshots/$id.tgz" -p "$out/.dl" -q >/dev/null 2>&1 || { echo "FAIL download $id"; continue; }
  mkdir -p "$out/$id.part"
  tar xzf "$out/.dl/$id.tgz" -C "$out/$id.part" --wildcards '*.py' --exclude='.git' --exclude='*/.git/*' 2>/dev/null
  rm -f "$out/.dl/$id.tgz"
  mv "$out/$id.part" "$out/$id"
  echo "ok $id $(find "$out/$id" -name '*.py' | wc -l) files"
done
echo DONE
