#!/usr/bin/env sh
set -e

if [ "${ENABLE_GUI:-0}" = "1" ]; then
  echo "[run_lb] GUI=ON → load_balancer_gui.py"
  exec python -u load_balancer_gui.py
else
  echo "[run_lb] GUI=OFF → load_balancer_FD.py"
  exec python -u load_balancer_FD.py
fi
