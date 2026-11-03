#!/usr/bin/env bash
# BlindNav 真机影子链路一键跑：装包 → 授权 → 清相机占用 → 拉起调试屏 → 同时抓 logcat。
#
# 用法：
#   tools/android/run_ar_probe.sh                      # 只装包 + 拉起调试屏（开关用屏幕勾选）
#   tools/android/run_ar_probe.sh --full                # 打开全部影子开关并自动启动
#   tools/android/run_ar_probe.sh --flags "ENABLE_ARCORE_OWNER ENABLE_SENSOR_LOG"
#   tools/android/run_ar_probe.sh --seconds 600         # 跑 10 分钟后自动停止并拉日志
#
# 为什么先 force-stop：CameraActivity 用 CameraX 独占后摄，ARCore 起不来时
# 第一个表现就是 session.update() 抛 CameraNotAvailableException。
set -euo pipefail

PKG=com.blindnav.nav
ACTIVITY="$PKG/com.blindnav.app.shadow.ArShadowActivity"
APK="${APK:-app/build/outputs/apk/debug/app-debug.apk}"
ADB="${ADB:-adb}"
SECONDS_TO_RUN=0
FLAGS=""
FULL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apk) APK="$2"; shift 2 ;;
    --seconds) SECONDS_TO_RUN="$2"; shift 2 ;;
    --flags) FLAGS="$2"; shift 2 ;;
    --full) FULL=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ $FULL -eq 1 ]]; then
  FLAGS="ENABLE_ARCORE_OWNER ENABLE_SENSOR_LOG ENABLE_TRAVERSABILITY_EXPERIMENT ENABLE_WORLDPATH_SHADOW ENABLE_SAFE_MOTION_SHADOW"
fi

if ! $ADB get-state >/dev/null 2>&1; then
  echo "DEVICE_BLOCKER: 没有连接任何 adb 设备。插上手机并允许调试后重试。" >&2
  exit 3
fi

$ADB shell getprop ro.product.model | tr -d '\r' | sed 's/^/device=/
'
echo "ARCore APK 是否已安装："
$ADB shell pm list packages com.google.ar.core || echo "  未安装 —— 影子屏第一次启动会请求安装 Google Play Services for AR"

if [[ -f "$APK" ]]; then
  echo "install $APK"
  $ADB install -r -g "$APK"
else
  echo "跳过安装（找不到 $APK）"
fi

$ADB shell pm grant "$PKG" android.permission.CAMERA || true
$ADB shell pm grant "$PKG" android.permission.ACCESS_FINE_LOCATION || true
$ADB shell am force-stop "$PKG"

# 影子开关会改变相机所有权，debug 包的调试屏是 exported=true，所以必须带口令。
# 与 ArShadowActivity.SHADOW_TOKEN 保持一致。
SHADOW_TOKEN="${SHADOW_TOKEN:-blindnav-shadow-2026}"
EXTRA=(--es shadow_token "$SHADOW_TOKEN")
for f in $FLAGS; do EXTRA+=(--ez "$f" true); done
if [[ $SECONDS_TO_RUN -gt 0 ]]; then EXTRA+=(--ez auto_start true); fi

LOG_DIR="shadow-runs/$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"
echo "日志目录：$LOG_DIR"

$ADB logcat -c || true
$ADB logcat -v time -s BlindNavShadow:V AndroidRuntime:E System.err:W ARCoreLog:V \
  > "$LOG_DIR/logcat.txt" 2>&1 &
LOGCAT_PID=$!
trap 'kill $LOGCAT_PID 2>/dev/null || true' EXIT

$ADB shell am start -n "$ACTIVITY" "${EXTRA[@]}"

if [[ $SECONDS_TO_RUN -gt 0 ]]; then
  echo "运行 ${SECONDS_TO_RUN}s，期间按 checklist 做动作…"
  sleep "$SECONDS_TO_RUN"
  $ADB shell input keyevent KEYCODE_BACK || true
  sleep 2
fi

echo "拉取影子日志…"
$ADB pull "/sdcard/Android/data/$PKG/files/shadowlog" "$LOG_DIR/shadowlog" || \
  echo "  （没有日志文件：影子开关没打开，或 App 在写盘前被杀）"
$ADB pull "/sdcard/Android/data/$PKG/files/last10m" "$LOG_DIR/last10m" 2>/dev/null || true

echo "完成。分析：python3 tools/android/analyze_shadow_log.py $LOG_DIR/shadowlog"
