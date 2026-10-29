#!/bin/bash
# usage: start_private_comfyui.sh [noocio]  -- private ComfyUI for OpenClip acceptance; port 8199, CPU only.
# Runs the shared install in /opt/ai/comfyui read-only with its own base directory ($OC_ACCEPT/base,
# default ~/oc_accept/base); log in $OC_ACCEPT/server.log (server_noocio.log for noocio).
OC_ACCEPT=${OC_ACCEPT:-$HOME/oc_accept}
cd /opt/ai/comfyui || exit 1
if [ "$1" = noocio ]; then mkdir -p "$OC_ACCEPT/blockocio"; echo "raise ImportError(\"blocked for test\")" > "$OC_ACCEPT/blockocio/PyOpenColorIO.py"; export PYTHONPATH="$OC_ACCEPT/blockocio"; fi
exec /home/aiuser/opt2/anaconda3/envs/comfyui/bin/python main.py --base-directory "$OC_ACCEPT/base" --port 8199 --cpu --disable-api-nodes > "$OC_ACCEPT/server${1:+_$1}.log" 2>&1
