#!/bin/sh
# Copy only reviewed source; private tokens are never bundled here.
set -eu
opf_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
opf_target="$opf_dir/../../data/homeassistant/custom_components/opf_status"
mkdir -p "$opf_target"
for opf_file in "$opf_dir"/custom_components/opf_status/*.py "$opf_dir"/custom_components/opf_status/*.json; do
  cp "$opf_file" "$opf_target/"
done
printf '%s\n' 'OPF component copied. Check HA configuration before restarting.'
