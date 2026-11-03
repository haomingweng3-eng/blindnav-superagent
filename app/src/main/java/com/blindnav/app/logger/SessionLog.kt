package com.blindnav.app.logger

import java.nio.ByteBuffer
import java.util.Locale
import kotlin.math.tan

/*
 * Android-free half of the session logger.
 *
 * meta.json / camera.json serialisation, the intrinsics estimate and the YUV->NV21 packing all
 * live here so they can be covered by plain JVM unit tests ("org.json" and android.graphics are
 * stubs under test). SessionCaptureActivity only reads platform values and hands them over.
 */

internal object Json {
    fun quote(value: String): String {
        val sb = StringBuilder(value.length + 2)
        sb.append('"')
        for (ch in value) {
            when (ch) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                '\b' -> sb.append("\\b")
                '\u000C' -> sb.append("\\f")
                else -> if (ch < ' ') sb.append(String.format(Locale.US, "\\u%04x", ch.code)) else sb.append(ch)
            }
        }
        sb.append('"')
        return sb.toString()
    }

    /** JSON has no NaN/Infinity; absent numeric values must serialise as `null`. */
    fun number(value: Double?): String =
        if (value == null || !value.isFinite()) "null" else value.toString()
}

/** Where the recorded pixel coordinates are anchored. */
internal const val PIXEL_COORDINATES_ACTIVE_ARRAY = "active_array"

/**
 * Camera intrinsics in active-array pixels. `width`/`height` are the sensor array the intrinsics
 * refer to, **not** the analysis stream: the PC loader rescales K by `video_size / width,height`.
 */
internal data class CameraIntrinsics(
    val fx: Double,
    val fy: Double,
    val cx: Double,
    val cy: Double,
    val width: Int,
    val height: Int,
    val source: String,
    val cameraHeightM: Double,
    val focalLengthMm: Double? = null,
    val sensorWidthMm: Double? = null,
    val sensorHeightMm: Double? = null,
    val skew: Double? = null,
    val note: String? = null,
) {
    val usable: Boolean
        get() = width > 0 && height > 0 && fx > 0.0 && fy > 0.0

    fun toJson(frame: FrameGeometry?): String = buildString {
        append('{')
        append("\"fx\":").append(Json.number(fx))
        append(",\"fy\":").append(Json.number(fy))
        append(",\"cx\":").append(Json.number(cx))
        append(",\"cy\":").append(Json.number(cy))
        append(",\"width\":").append(width)
        append(",\"height\":").append(height)
        append(",\"camera_height_m\":").append(Json.number(cameraHeightM))
        append(",\"intrinsics_source\":").append(Json.quote(source))
        append(",\"pixel_coordinates\":").append(Json.quote(PIXEL_COORDINATES_ACTIVE_ARRAY))
        append(",\"analysis_width\":").append(frame?.width ?: 0)
        append(",\"analysis_height\":").append(frame?.height ?: 0)
        frame?.rotationDeg?.let { append(",\"frame_rotation_deg\":").append(it) }
        focalLengthMm?.let { append(",\"focal_length_mm\":").append(Json.number(it)) }
        sensorWidthMm?.let { append(",\"sensor_width_mm\":").append(Json.number(it)) }
        sensorHeightMm?.let { append(",\"sensor_height_mm\":").append(Json.number(it)) }
        skew?.let { append(",\"skew\":").append(Json.number(it)) }
        note?.let { append(",\"note\":").append(Json.quote(it)) }
        append('}')
    }
}

internal data class FrameGeometry(val width: Int, val height: Int, val rotationDeg: Int?)

internal object IntrinsicsEstimator {
    const val FROM_CALIBRATION = "calibration"
    const val FROM_FOCAL_MM = "estimated_focal_mm"
    const val FROM_FOV = "estimated_fov"

    const val DEFAULT_HFOV_DEG = 70.0

    /**
     * Priority: device calibration -> focal length + physical sensor size -> 70 deg horizontal FOV.
     * The fallbacks never emit zeros (a zero fx makes the PC ground calibration divide by zero);
     * they are flagged through [CameraIntrinsics.source] and [CameraIntrinsics.note].
     */
    fun estimate(
        calibration: DoubleArray?,
        activeWidth: Int,
        activeHeight: Int,
        focalLengthMm: Double?,
        sensorWidthMm: Double?,
        sensorHeightMm: Double?,
        cameraHeightM: Double,
    ): CameraIntrinsics {
        val w = activeWidth.coerceAtLeast(0)
        val h = activeHeight.coerceAtLeast(0)
        val cx = if (w > 0) w / 2.0 else 0.0
        val cy = if (h > 0) h / 2.0 else 0.0

        if (calibration != null && calibration.size >= 4 && w > 0 && h > 0) {
            val fx = calibration[0]
            val fy = calibration[1]
            val px = calibration[2]
            val py = calibration[3]
            if (fx.isFinite() && fy.isFinite() && px.isFinite() && py.isFinite() && fx > 0.0 && fy > 0.0) {
                val skew = calibration.getOrNull(4)?.takeIf { it.isFinite() && it != 0.0 }
                return CameraIntrinsics(fx, fy, px, py, w, h, FROM_CALIBRATION, cameraHeightM,
                    focalLengthMm, sensorWidthMm, sensorHeightMm, skew)
            }
        }

        if (focalLengthMm != null && focalLengthMm.isFinite() && focalLengthMm > 0.0 &&
            sensorWidthMm != null && sensorWidthMm.isFinite() && sensorWidthMm > 0.0 &&
            sensorHeightMm != null && sensorHeightMm.isFinite() && sensorHeightMm > 0.0 &&
            w > 0 && h > 0
        ) {
            // Pin-hole: f_px = f_mm / sensor_mm * pixels.  Assumes the analysis stream covers the
            // full active array; camera.json records analysis_width/height so this can be checked.
            val fx = focalLengthMm / sensorWidthMm * w
            val fy = focalLengthMm / sensorHeightMm * h
            return CameraIntrinsics(fx, fy, cx, cy, w, h, FROM_FOCAL_MM, cameraHeightM,
                focalLengthMm, sensorWidthMm, sensorHeightMm,
                note = "estimated from focal length and physical sensor size")
        }

        val fx = if (w > 0) (w / 2.0) / tan(Math.toRadians(DEFAULT_HFOV_DEG) / 2.0) else 0.0
        return CameraIntrinsics(fx, fx, cx, cy, w, h, FROM_FOV, cameraHeightM,
            focalLengthMm, sensorWidthMm, sensorHeightMm,
            note = "no calibration or focal length; assumed ${DEFAULT_HFOV_DEG.toInt()} deg horizontal FOV")
    }
}

internal data class DeviceInfo(
    val manufacturer: String,
    val model: String,
    val device: String,
    val androidSdk: Int,
    val abi: String?,
    val appVersion: String?,
)

/**
 * Mutable per-session counters. Every method is called from SessionWriter's synchronised block,
 * so no extra locking is needed here.
 */
internal class SessionStats(
    val sessionId: String,
    val createdUtc: String,
    val device: DeviceInfo,
    val fpsTarget: Int,
    val cameraHeightM: Double,
    val cameraId: String?,
    val startElapsedNs: Long,
    val availableSensors: List<String>,
) {
    var endUtc: String? = null
    var endElapsedNs: Long? = null
    var frames = 0L
    var frameErrors = 0L
    var frameWidth = 0
    var frameHeight = 0
    var frameRotationDeg: Int? = null
    var intrinsicsSource: String? = null
    var stepsDetected = 0L
    var stepCounterStart: Double? = null
    var stepCounterLast: Double? = null
    var hasStepDetector = false
    var stepPermissionGranted = false

    private val sensorRows = LinkedHashMap<String, Long>()

    fun addSensorRow(sensorType: String) {
        sensorRows[sensorType] = (sensorRows[sensorType] ?: 0L) + 1L
    }

    val sensorRowCount: Long get() = sensorRows.values.sum()

    fun toJson(): String {
        val durationS = endElapsedNs?.let { (it - startElapsedNs) / 1e9 }
        return buildString {
            append("{\n")
            append("  \"schema_version\": 1,\n")
            append("  \"session_id\": ").append(Json.quote(sessionId)).append(",\n")
            append("  \"created_utc\": ").append(Json.quote(createdUtc)).append(",\n")
            endUtc?.let { append("  \"ended_utc\": ").append(Json.quote(it)).append(",\n") }
            append("  \"duration_s\": ").append(Json.number(durationS)).append(",\n")
            append("  \"clock\": {\n")
            append("    \"timestamp_source\": \"elapsedRealtimeNanos/sensor_event_timestamp\",\n")
            append("    \"start_elapsed_ns\": ").append(startElapsedNs).append(",\n")
            append("    \"end_elapsed_ns\": ").append(endElapsedNs?.toString() ?: "null").append(",\n")
            append("    \"steps_t_reference\": \"session_start\"\n")
            append("  },\n")
            append("  \"device\": {\n")
            append("    \"manufacturer\": ").append(Json.quote(device.manufacturer)).append(",\n")
            append("    \"model\": ").append(Json.quote(device.model)).append(",\n")
            append("    \"device\": ").append(Json.quote(device.device)).append(",\n")
            append("    \"android_sdk\": ").append(device.androidSdk).append(",\n")
            append("    \"abi\": ").append(device.abi?.let { Json.quote(it) } ?: "null").append(",\n")
            append("    \"app_version\": ").append(device.appVersion?.let { Json.quote(it) } ?: "null").append("\n")
            append("  },\n")
            append("  \"capture\": {\n")
            append("    \"fps_target\": ").append(fpsTarget).append(",\n")
            append("    \"camera_height_m\": ").append(Json.number(cameraHeightM)).append(",\n")
            append("    \"camera_id\": ").append(cameraId?.let { Json.quote(it) } ?: "null").append(",\n")
            append("    \"frames\": ").append(frames).append(",\n")
            append("    \"frame_errors\": ").append(frameErrors).append(",\n")
            append("    \"frame_width\": ").append(frameWidth).append(",\n")
            append("    \"frame_height\": ").append(frameHeight).append(",\n")
            append("    \"frame_rotation_deg\": ").append(frameRotationDeg?.toString() ?: "null").append(",\n")
            append("    \"intrinsics_file\": \"camera.json\",\n")
            append("    \"intrinsics_source\": ").append(intrinsicsSource?.let { Json.quote(it) } ?: "null").append("\n")
            append("  },\n")
            append("  \"sensors\": {\n")
            append("    \"available\": [")
            append(availableSensors.joinToString(", ") { Json.quote(it) })
            append("],\n")
            append("    \"row_count\": ").append(sensorRowCount).append(",\n")
            append("    \"rows_by_type\": {")
            append(sensorRows.entries.joinToString(", ") { (type, n) -> Json.quote(type) + ": " + n })
            append("}\n")
            append("  },\n")
            append("  \"steps\": {\n")
            append("    \"detector_available\": ").append(hasStepDetector).append(",\n")
            append("    \"activity_recognition_permission\": ").append(stepPermissionGranted).append(",\n")
            append("    \"detector_events\": ").append(stepsDetected).append(",\n")
            append("    \"counter_start\": ").append(Json.number(stepCounterStart)).append(",\n")
            append("    \"counter_last\": ").append(Json.number(stepCounterLast)).append(",\n")
            val counterStart = stepCounterStart
            val counterLast = stepCounterLast
            append("    \"counter_delta\": ")
            append(Json.number(if (counterStart != null && counterLast != null) counterLast - counterStart else null))
            append(",\n")
            append("    \"file\": ").append(if (hasStepDetector) Json.quote("steps.csv") else "null").append("\n")
            append("  }\n")
            append("}")
        }
    }
}

internal data class ImagePlane(val buffer: ByteBuffer, val rowStride: Int, val pixelStride: Int)

/**
 * Pack YUV_420_888 planes into NV21, honouring row and pixel stride.
 *
 * Ignoring stride is the classic bug here: CameraX hands out buffers whose rows are padded, so a
 * naive packed copy shears the saved JPEG diagonally and the resulting frames no longer match
 * camera.json. Out-of-range reads fall back to neutral chroma / black luma instead of throwing.
 */
internal fun yuv420ToNv21(width: Int, height: Int, y: ImagePlane, u: ImagePlane, v: ImagePlane): ByteArray {
    require(width > 0 && height > 0) { "image size must be positive, got ${width}x$height" }
    val chromaWidth = (width + 1) / 2
    val chromaHeight = (height + 1) / 2
    val out = ByteArray(width * height + 2 * chromaWidth * chromaHeight)
    var o = 0
    for (row in 0 until height) {
        val base = row * y.rowStride
        for (col in 0 until width) out[o++] = readOr(y.buffer, base + col * y.pixelStride, 0)
    }
    for (row in 0 until chromaHeight) {
        val vBase = row * v.rowStride
        val uBase = row * u.rowStride
        for (col in 0 until chromaWidth) {
            out[o++] = readOr(v.buffer, vBase + col * v.pixelStride, 128)
            out[o++] = readOr(u.buffer, uBase + col * u.pixelStride, 128)
        }
    }
    return out
}

private fun readOr(buffer: ByteBuffer, index: Int, fallback: Int): Byte =
    if (index in 0 until buffer.limit()) buffer.get(index) else fallback.toByte()

/** `steps.csv` is the PC `--steps` format: one session-relative `t_ms` value per detected step. */
internal const val STEPS_HEADER = "t_ms\n"

internal fun stepLine(timestampNs: Long, startNs: Long): String =
    String.format(Locale.US, "%.3f\n", relativeMs(timestampNs, startNs).coerceAtLeast(0.0))

internal fun relativeMs(timestampNs: Long, startNs: Long): Double = (timestampNs - startNs) / 1_000_000.0
