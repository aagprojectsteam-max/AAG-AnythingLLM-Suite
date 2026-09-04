#!/usr/bin/env bash
set -euo pipefail
arch=$(uname -m)
os=unknown; [[ -r /etc/os-release ]] && . /etc/os-release && os=${ID:-unknown}
ram_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
disk_kb=$(df -Pk "${1:-$PWD}" | awk 'NR==2{print $4}')
intel=NO; nvidia=NO; level_zero=NO; sycl=NO
if command -v lspci >/dev/null && lspci | grep -Eiq 'VGA|3D|Display'; then
  lspci | grep -Eiq 'Intel.*(VGA|3D|Display)|(VGA|3D|Display).*Intel' && intel=YES || true
  lspci | grep -Eiq 'NVIDIA' && nvidia=DETECTED_UNSUPPORTED_BY_PACKAGED_LOCAL_LLM || true
fi
command -v sycl-ls >/dev/null && sycl=YES
{ command -v zeinfo >/dev/null || compgen -G '/dev/dri/renderD*' >/dev/null; } && [[ $intel == YES ]] && level_zero=POSSIBLE
cat <<EOF
OS=$os
ARCH=$arch
RAM_MIB=$((ram_kb/1024))
DISK_FREE_MIB=$((disk_kb/1024))
INTEL_GPU=$intel
LEVEL_ZERO=$level_zero
SYCL=$sycl
NVIDIA_GPU=$nvidia
CPU_FALLBACK=SUPPORTED_FOR_CHESS_AND_CORE;LOCAL_LLM_REQUIRES_USER_LLAMA_CPP_BUILD
EOF

