package com.blindnav.app.logger

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.Bundle
import android.os.Environment
import android.view.Surface
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
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
import kotlin.math.roundToInt

/** Recording-only logger. It deliberately has no ARCore or inference dependency. */
class SessionCaptureActivity : Activity(), SensorEventListener {
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var sensorManager: SensorManager
    private var writer: SessionWriter? = null
    private var running = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val height = EditText(this).apply { hint = "camera height (m)"; setText("1.2") }
        val button = Button(this).apply { text = "Start capture" }
        setContentView(LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; addView(height); addView(button) })
        button.setOnClickListener {
            if (running) { stopCapture(); button.text = "Start capture" }
            else { startCapture(height.text.toString().toDoubleOrNull() ?: 1.2); button.text = "Stop capture" }
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), 7)
    }

    private fun startCapture(cameraHeight: Double) {
        val stamp = DateTimeFormatter.ISO_INSTANT.format(Instant.now()).replace(":", "-")
        writer = SessionWriter(File(getExternalFilesDir(null), "sessions/session-$stamp-${android.os.Process.myPid()}"), cameraHeight)
        val cm = getSystemService(Context.CAMERA_SERVICE) as CameraManager
        cm.cameraIdList.firstOrNull { id -> cm.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK }
            ?.let { id -> writer?.writeIntrinsics(cm.getCameraCharacteristics(id)) }
        running = true
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        listOf(Sensor.TYPE_GRAVITY, Sensor.TYPE_GYROSCOPE, Sensor.TYPE_GAME_ROTATION_VECTOR,
            Sensor.TYPE_STEP_DETECTOR, Sensor.TYPE_STEP_COUNTER).forEach { type ->
            sensorManager.getDefaultSensor(type)?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
        }
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val provider = future.get()
            val analysis = ImageAnalysis.Builder().setTargetRotation(Surface.ROTATION_0)
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
            analysis.setAnalyzer(executor) { image ->
                if (running) writer?.writeFrame(image)
                else image.close()
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
}

private class SessionWriter(private val dir: File, private val cameraHeight: Double) {
    private val frames = File(dir, "frames").apply { mkdirs() }
    private val imu: BufferedWriter
    private var frameNo = 0
    private var firstNs = 0L
    private var lastNs = 0L
    init {
        dir.mkdirs(); imu = BufferedWriter(OutputStreamWriter(FileOutputStream(File(dir, "imu.csv"))))
        imu.write("t_ns,sensor_type,x,y,z,w\n")
        File(dir, "meta.json").writeText("{\"device_model\":\"${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}\",\"android_sdk\":${android.os.Build.VERSION.SDK_INT},\"fps_target\":8,\"timestamp_source\":\"elapsedRealtimeNanos/sensor_event_timestamp\"}")
    }
    @Synchronized fun writeSensor(e: SensorEvent) {
        if (firstNs == 0L) firstNs = e.timestamp; lastNs = e.timestamp
        val v = e.values; val x = v.getOrElse(0) { 0f }; val y = v.getOrElse(1) { 0f }; val z = v.getOrElse(2) { 0f }; val w = v.getOrElse(3) { Float.NaN }
        imu.write("${e.timestamp},${e.sensor.stringType},$x,$y,$z,$w\n")
    }
    @Synchronized fun writeFrame(image: ImageProxy) {
        try {
            val y = image.planes[0].buffer; val u = image.planes[1].buffer; val v = image.planes[2].buffer
            val nv21 = ByteArray(image.width * image.height * 3 / 2)
            y.get(nv21, 0, minOf(image.width * image.height, y.remaining()))
            var p = image.width * image.height
            while (p < nv21.size && u.hasRemaining() && v.hasRemaining()) { nv21[p++] = v.get(); nv21[p++] = u.get() }
            val yuv = android.graphics.YuvImage(nv21, android.graphics.ImageFormat.NV21, image.width, image.height, null)
            val out = File(frames, String.format(Locale.US, "%06d.jpg", frameNo++))
            FileOutputStream(out).use { yuv.compressToJpeg(android.graphics.Rect(0, 0, image.width, image.height), 85, it) }
        } finally { image.close() }
    }
    @Synchronized fun writeIntrinsics(c: android.hardware.camera2.CameraCharacteristics) {
        val i = c.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION)
        val s = c.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE)
        if (i != null && i.size >= 4) File(dir, "camera.json").writeText("{\"fx\":${i[0]},\"fy\":${i[1]},\"cx\":${i[2]},\"cy\":${i[3]},\"width\":${s?.width ?: 0},\"height\":${s?.height ?: 0},\"camera_height_m\":$cameraHeight,\"timestamp_source\":\"elapsedRealtimeNanos/sensor_event_timestamp\"}")
    }
    @Synchronized fun close() {
        imu.flush(); imu.close()
        val camera = File(dir, "camera.json")
        if (!camera.exists()) camera.writeText("{\"fx\":0,\"fy\":0,\"cx\":0,\"cy\":0,\"width\":0,\"height\":0,\"camera_height_m\":$cameraHeight,\"timestamp_source\":\"elapsedRealtimeNanos/sensor_event_timestamp\",\"intrinsic_notes\":\"not exposed; estimate from focal length/sensor size\"}")
        File(dir, "meta.json").appendText("\n")
    }
}
