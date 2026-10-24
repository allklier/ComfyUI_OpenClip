#!/bin/bash
# usage: start.sh [noocio]  -- private ComfyUI for OpenClip acceptance; port 8199, CPU only
cd /opt/ai/comfyui
EXTRA=""
if [ "$1" = noocio ]; then mkdir -p ~/oc_accept/blockocio; echo "raise ImportError(\"blocked for test\")" > ~/oc_accept/blockocio/PyOpenColorIO.py; export PYTHONPATH=~/oc_accept/blockocio; fi
exec /home/aiuser/opt2/anaconda3/envs/comfyui/bin/python main.py --base-directory ~/oc_accept/base --port 8199 --cpu --disable-api-nodes > ~/oc_accept/server${1:+_$1}.log 2>&1
