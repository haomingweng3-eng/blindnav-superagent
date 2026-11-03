# Thin phone session logger

`SessionCaptureActivity` is recording-only: CameraX rear frames are written as JPEGs and
Android sensor events are written with their native `elapsedRealtimeNanos` timestamps. It
uses `TYPE_GRAVITY` (not a camera-pose Y-up assumption), gyroscope, game rotation vector,
step detector/counter, and records the user-settable camera height (default 1.2 m).

On a Xiaomi 18 Pro debug build:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell pm grant com.blindnav.nav android.permission.CAMERA
adb shell am start -n com.blindnav.nav/com.blindnav.app.logger.SessionCaptureActivity
adb shell dumpsys sensorservice | head
adb shell run-as com.blindnav.nav ls files  # external files are not run-as readable on every build
adb shell find /sdcard/Android/data/com.blindnav.nav/files/sessions -maxdepth 3 -type f
adb pull /sdcard/Android/data/com.blindnav.nav/files/sessions ./sessions
```

The session contains `frames/000000.jpg`, `imu.csv`, `camera.json`, and `meta.json`.
Camera intrinsics should be filled from `CameraCharacteristics.LENS_INTRINSIC_CALIBRATION`
when the device exposes them; otherwise use the focal-length/sensor-size estimate in the PC
loader. No API keys, inference, ARCore, or Google Play Services are required for logging.
