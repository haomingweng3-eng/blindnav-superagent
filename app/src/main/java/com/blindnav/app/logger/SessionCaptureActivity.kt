package com.blindnav.app.logger

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import androidx.activity.ComponentActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.io.BufferedWriter
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStreamWriter
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.concurrent.Executors

/**
 * Recording-only logger. It deliberately has no ARCore or inference dependency.
 *
 * Session layout (see tools/android/LOGGER.md): `frames/NNNNNN.jpg`, `imu.csv` (native
 * elapsedRealtimeNanos), `steps.csv` (session-relative t_ms, only when TYPE_STEP_DETECTOR exists),
 * `camera.json` (active-array intrinsics) and `meta.json` (device, counters, clock).
 */
class SessionCaptureActivity : ComponentActivity(), SensorEventListener {
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var sensorManager: SensorManager
    private var writer: SessionWriter? = null
    private var running = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val height = EditText(this).apply { hint = "camera height (m)"; setText(DEFAULT_CAMERA_HEIGHT.toString()) }
        val button = Button(this).apply { text = "Start capture" }
        setContentView(LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; addView(height); addView(button) })
        button.setOnClickListener {
            if (running) { stopCapture(); button.text = "Start capture" }
            else { startCapture(height.text.toString().toDoubleOrNull() ?: DEFAULT_CAMERA_HEIGHT); button.text = "Stop capture" }
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), 7)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACTIVITY_RECOGNITION) != PackageManager.PERMISSION_GRANTED
        ) ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.ACTIVITY_RECOGNITION), 8)
    }

    private fun startCapture(requestedHeight: Double) {
        val cameraHeight = requestedHeight.takeIf { it.isFinite() && it > 0.0 } ?: DEFAULT_CAMERA_HEIGHT
        val stamp = DateTimeFormatter.ISO_INSTANT.format(Instant.now()).replace(":", "-")
        val sessionId = "session-$stamp-${android.os.Process.myPid()}"
        val dir = File(getExternalFilesDir(null), "sessions/$sessionId")

        val cm = getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val backId = cm.cameraIdList.firstOrNull { id ->
            cm.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK
        }
        val characteristics = backId?.let { runCatching { cm.getCameraCharacteristics(it) }.getOrNull() }
        val intrinsics = characteristics?.let { readIntrinsics(it, cameraHeight) }
            ?: IntrinsicsEstimator.estimate(null, 0, 0, null, null, null, cameraHeight)

        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val sensors = SENSOR_TYPES.mapNotNull { sensorManager.getDefaultSensor(it) }
        writer = SessionWriter(dir, sessionId, cameraHeight, backId, sensors, intrinsics,
            appVersion(), stepRecognitionGranted())
        sensors.forEach { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
        running = true

        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val provider = future.get()
            val analysis = ImageAnalysis.Builder()
                .setTargetRotation(Surface.ROTATION_0)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(executor) { image ->
                val session = writer
                if (running && session != null && session === writer) session.writeFrame(image) else image.close()
            }
            provider.unbindAll()
            provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, analysis)
        }, ContextCompat.getMainExecutor(this))
    }

    private fun stopCapture() {
        running = false
        if (::sensorManager.isInitialized) sensorManager.unregisterListener(this)
        writer?.close(); writer = null
    }

    override fun onDestroy() { stopCapture(); executor.shutdown(); super.onDestroy() }
    override fun onSensorChanged(e: SensorEvent) { if (running) writer?.writeSensor(e) }
    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    private fun appVersion(): String? = runCatching {
        packageManager.getPackageInfo(packageName, 0).versionName
    }.getOrNull()

    /** TYPE_STEP_* deliver nothing on Android 10+ without this runtime permission. */
    private fun stepRecognitionGranted(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACTIVITY_RECOGNITION) == PackageManager.PERMISSION_GRANTED

    /**
     * LENS_INTRINSIC_CALIBRATION is `[fx, fy, cx, cy, s]` in active-array pixels. When the device
     * does not expose it, fall back to focal length / physical sensor size, then to a 70 deg FOV.
     */
    private fun readIntrinsics(c: CameraCharacteristics, cameraHeight: Double): CameraIntrinsics {
        val active = c.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE)
        val pixel = c.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE)
        val width = active?.width() ?: pixel?.width ?: 0
        val height = active?.height() ?: pixel?.height ?: 0
        val calibration = c.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION)
            ?.let { values -> DoubleArray(values.size) { values[it].toDouble() } }
        val focalMm = c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)?.firstOrNull()?.toDouble()
        val physical = c.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE)
        return IntrinsicsEstimator.estimate(calibration, width, height, focalMm,
            physical?.width?.toDouble(), physical?.height?.toDouble(), cameraHeight)
    }

    companion object {
        private const val DEFAULT_CAMERA_HEIGHT = 1.2
        private val SENSOR_TYPES = listOf(
            Sensor.TYPE_GRAVITY, Sensor.TYPE_GYROSCOPE, Sensor.TYPE_GAME_ROTATION_VECTOR,
            Sensor.TYPE_STEP_DETECTOR, Sensor.TYPE_STEP_COUNTER,
        )
    }
}

private class SessionWriter(
    dir: File,
    private val sessionId: String,
    private val cameraHeight: Double,
    private val cameraId: String?,
    sensors: List<Sensor>,
    private val intrinsics: CameraIntrinsics,
    appVersion: String?,
    private val stepPermissionGranted: Boolean,
) {
    private val dir = dir.apply { mkdirs() }
    private val frames = File(this.dir, "frames").apply { mkdirs() }
    private val cameraJson = File(this.dir, "camera.json")
    private val metaJson = File(this.dir, "meta.json")
    private val startNs = SystemClock.elapsedRealtimeNanos()
    private val stepDetectorAvailable = sensors.any { it.type == Sensor.TYPE_STEP_DETECTOR }
    private val stats = SessionStats(
        sessionId = sessionId,
        createdUtc = Instant.now().toString(),
        device = DeviceInfo(
            manufacturer = Build.MANUFACTURER ?: "unknown",
            model = Build.MODEL ?: "unknown",
            device = Build.DEVICE ?: "unknown",
            androidSdk = Build.VERSION.SDK_INT,
            abi = Build.SUPPORTED_ABIS?.firstOrNull(),
            appVersion = appVersion,
        ),
        fpsTarget = FPS_TARGET,
        cameraHeightM = cameraHeight,
        cameraId = cameraId,
        startElapsedNs = startNs,
        availableSensors = sensors.map { it.stringType ?: it.name },
    )
    private val imu = BufferedWriter(OutputStreamWriter(FileOutputStream(File(this.dir, "imu.csv"))), 64 * 1024)
    private val steps: BufferedWriter? = if (stepDetectorAvailable)
        BufferedWriter(OutputStreamWriter(FileOutputStream(File(this.dir, "steps.csv"))), 8 * 1024) else null
    private var pendingRows = 0
    private var closed = false

    init {
        stats.hasStepDetector = stepDetectorAvailable
        stats.stepPermissionGranted = stepPermissionGranted
        imu.write("t_ns,sensor_type,x,y,z,w\n")
        steps?.write(STEPS_HEADER)
        writeCameraJson()   // also records stats.intrinsicsSource, so write meta after it
        writeMeta()
    }

    @Synchronized fun writeSensor(e: SensorEvent) {
        if (closed) return
        val type = e.sensor.stringType ?: e.sensor.name
        val values = e.values
        imu.write(e.timestamp.toString())
        imu.write(",")
        imu.write(type)
        for (i in 0 until 4) {
            imu.write(",")
            imu.write(if (i < values.size) values[i].toString() else "NaN")
        }
        imu.write("\n")
        stats.addSensorRow(type)
        when (e.sensor.type) {
            Sensor.TYPE_STEP_DETECTOR -> {
                stats.stepsDetected += 1
                steps?.write(stepLine(e.timestamp, startNs))
            }
            Sensor.TYPE_STEP_COUNTER -> {
                val count = values.firstOrNull()?.toDouble()
                if (count != null && count.isFinite()) {
                    if (stats.stepCounterStart == null) stats.stepCounterStart = count
                    stats.stepCounterLast = count
                }
            }
        }
        if (++pendingRows >= FLUSH_EVERY) flush()
    }

    @Synchronized fun writeFrame(image: ImageProxy) {
        if (closed) { image.close(); return }
        try {
            val planes = image.planes
            val nv21 = yuv420ToNv21(image.width, image.height,
                ImagePlane(planes[0].buffer, planes[0].rowStride, planes[0].pixelStride),
                ImagePlane(planes[1].buffer, planes[1].rowStride, planes[1].pixelStride),
                ImagePlane(planes[2].buffer, planes[2].rowStride, planes[2].pixelStride))
            val yuv = YuvImage(nv21, ImageFormat.NV21, image.width, image.height, null)
            val out = File(frames, String.format(Locale.US, "%06d.jpg", stats.frames))
            val ok = FileOutputStream(out).use {
                yuv.compressToJpeg(Rect(0, 0, image.width, image.height), JPEG_QUALITY, it)
            }
            if (ok) {
                stats.frames += 1
                stats.frameWidth = image.width
                stats.frameHeight = image.height
                stats.frameRotationDeg = image.imageInfo.rotationDegrees
            } else {
                stats.frameErrors += 1
                out.delete()
            }
        } catch (e: Exception) {
            stats.frameErrors += 1
            Log.w(TAG, "frame encode failed: ${e.message}")
        } finally {
            image.close()
        }
    }

    @Synchronized fun close() {
        if (closed) return
        closed = true
        flush()
        stats.endElapsedNs = SystemClock.elapsedRealtimeNanos()
        stats.endUtc = Instant.now().toString()
        runCatching { imu.close() }.onFailure { Log.w(TAG, "imu close failed: ${it.message}") }
        runCatching { steps?.close() }.onFailure { Log.w(TAG, "steps close failed: ${it.message}") }
        writeCameraJson()
        writeMeta()
    }

    private fun flush() {
        runCatching { imu.flush() }.onFailure { Log.w(TAG, "imu flush failed: ${it.message}") }
        runCatching { steps?.flush() }.onFailure { Log.w(TAG, "steps flush failed: ${it.message}") }
        pendingRows = 0
    }

    private fun frameGeometry(): FrameGeometry? =
        if (stats.frameWidth > 0 && stats.frameHeight > 0)
            FrameGeometry(stats.frameWidth, stats.frameHeight, stats.frameRotationDeg) else null

    /** If the camera exposed no usable size, estimate at the analysis resolution once it is known. */
    private fun effectiveIntrinsics(): CameraIntrinsics =
        if (!intrinsics.usable && stats.frameWidth > 0 && stats.frameHeight > 0)
            IntrinsicsEstimator.estimate(null, stats.frameWidth, stats.frameHeight,
                intrinsics.focalLengthMm, intrinsics.sensorWidthMm, intrinsics.sensorHeightMm, cameraHeight)
        else intrinsics

    private fun writeCameraJson() {
        val effective = effectiveIntrinsics()
        stats.intrinsicsSource = effective.source
        writeAtomically(cameraJson, effective.toJson(frameGeometry()))
    }

    private fun writeMeta() = writeAtomically(metaJson, stats.toJson())

    private fun writeAtomically(file: File, text: String) {
        val tmp = File(file.parentFile, file.name + ".tmp")
        try {
            tmp.writeText(text)
            if (!tmp.renameTo(file)) {
                file.writeText(text)
                tmp.delete()
            }
        } catch (e: Exception) {
            Log.w(TAG, "write ${file.name} failed: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "SessionCapture"
        private const val FPS_TARGET = 8
        private const val JPEG_QUALITY = 85
        private const val FLUSH_EVERY = 256
    }
}
