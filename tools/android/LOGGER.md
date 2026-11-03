# Thin phone session logger

`SessionCaptureActivity` is recording-only: CameraX rear frames are written as JPEGs and
Android sensor events are written with their native `elapsedRealtimeNanos` timestamps. It
uses `TYPE_GRAVITY` (not a camera-pose Y-up assumption), gyroscope, game rotation vector,
step detector/counter, and records the user-settable camera height (default 1.2 m).

On a Xiaomi 18 Pro debug build:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell pm grant com.blindnav.nav android.permission.CAMERA
adb shell pm grant com.blindnav.nav android.permission.ACTIVITY_RECOGNITION  # Android 10+: step detector/counter
adb shell am start -n com.blindnav.nav/com.blindnav.app.logger.SessionCaptureActivity
adb shell dumpsys sensorservice | head
adb shell run-as com.blindnav.nav ls files  # external files are not run-as readable on every build
adb shell find /sdcard/Android/data/com.blindnav.nav/files/sessions -maxdepth 3 -type f
adb pull /sdcard/Android/data/com.blindnav.nav/files/sessions ./sessions
```

The session contains `frames/000000.jpg`, `imu.csv`, `steps.csv` (only when the device has a
`TYPE_STEP_DETECTOR`), `camera.json`, and `meta.json`:

- `imu.csv`: `t_ns,sensor_type,x,y,z,w`, native `SensorEvent.timestamp` (elapsedRealtimeNanos).
  Only `android.sensor.gravity` rows are needed for the PC ground calibration; missing components
  are written as `NaN`, extra sensors are ignored by the loader.
- `steps.csv`: `t_ms`, one row per `TYPE_STEP_DETECTOR` event, **relative to session start**
  (`meta.json` -> `clock.start_elapsed_ns`). This is directly usable as the PC `--steps` file;
  without the sensor the file is not created, so omit `--steps` instead of passing an empty log.
  On Android 10+ the `ACTIVITY_RECOGNITION` runtime permission is required; `meta.json` ->
  `steps.activity_recognition_permission` records whether the session had it (`detector_available`
  without it means the sensor exists but delivered no events).
- `camera.json`: `fx/fy/cx/cy` plus `width/height` in **active-array pixels** (the PC loader
  rescales K by `video_size / width,height`), `camera_height_m`, `intrinsics_source`, and
  `analysis_width/height` of the saved JPEGs. Intrinsics come from
  `LENS_INTRINSIC_CALIBRATION` when the device exposes it; otherwise they are estimated from
  `LENS_INFO_AVAILABLE_FOCAL_LENGTHS` + `SENSOR_INFO_PHYSICAL_SIZE`, and as a last resort from a
  70° horizontal FOV. `intrinsics_source` says which path was taken (`calibration`,
  `estimated_focal_mm`, `estimated_fov`) - never assume the numbers are calibrated.
- `meta.json`: `schema_version`, session id, wall-clock start/end, device model/SDK/ABI/app
  version, `clock` (native start/end ns and the `steps_t_reference`), `capture` (camera id,
  camera height, frame count/errors/size/rotation, intrinsics source), `sensors` (available
  types, row count per type) and `steps` (detector availability, detected events, the
  hardware step-counter start/last/delta). It is written at start and rewritten at stop, so a
  killed session still has the device and camera block.

`frames/*.jpg` are saved from the **unrotated** analysis buffer at the analysis resolution
(`camera.json` -> `analysis_width/height`), so K refers to that buffer. `capture.frame_rotation_deg`
/ `camera.json` -> `frame_rotation_deg` is the clockwise rotation CameraX reports to display the
buffer upright (typically 90 on a phone back camera). The PC loader's
`android_gravity_to_camera` assumes an already upright image, so when this value is non-zero,
roll the gravity vector by the same amount (or rotate the frames and K consistently) before
metric ground calibration - do not ignore it.

For the PC pipeline the relevant flags are `--imu <session>/imu.csv --camera
<session>/camera.json --steps <session>/steps.csv --height <meta capture.camera_height_m>`.
Convert `frames/` to a video first (e.g. ffmpeg at the recorded `fps_target`).

No API keys, inference, ARCore, or Google Play Services are required for logging.
