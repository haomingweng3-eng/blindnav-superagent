package com.blindnav.app.logger

import com.blindnav.app.last10m.MiniJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import kotlin.math.tan

/** JVM tests for the Android-free half of the session logger (meta/camera JSON, intrinsics, NV21). */
class SessionLogTest {

    private fun obj(json: String): Map<String, Any?> = MiniJson.parseObject(json)!!

    @Suppress("UNCHECKED_CAST")
    private fun Map<String, Any?>.obj(key: String): Map<String, Any?> = this[key] as Map<String, Any?>

    private fun Map<String, Any?>.num(key: String): Double = this[key] as Double

    private fun bb(vararg values: Int): ByteBuffer =
        ByteBuffer.wrap(ByteArray(values.size) { values[it].toByte() })

    @Test
    fun intrinsicsFromCalibrationAreUsedVerbatim() {
        val i = IntrinsicsEstimator.estimate(
            doubleArrayOf(1000.0, 990.0, 320.5, 240.5, 0.0), 640, 480, 4.0, 5.76, 4.29, 1.2)
        assertEquals(IntrinsicsEstimator.FROM_CALIBRATION, i.source)
        assertEquals(1000.0, i.fx, 1e-9)
        assertEquals(990.0, i.fy, 1e-9)
        assertEquals(320.5, i.cx, 1e-9)
        assertEquals(240.5, i.cy, 1e-9)
        assertEquals(640, i.width)
        assertEquals(480, i.height)
        assertTrue(i.usable)
        assertNull(i.skew)
    }

    @Test
    fun intrinsicsEstimatedFromFocalLengthAndPhysicalSize() {
        val i = IntrinsicsEstimator.estimate(null, 4032, 3024, 4.0, 5.76, 4.29, 1.2)
        assertEquals(IntrinsicsEstimator.FROM_FOCAL_MM, i.source)
        assertEquals(4.0 / 5.76 * 4032, i.fx, 1e-9)
        assertEquals(4.0 / 4.29 * 3024, i.fy, 1e-9)
        assertEquals(2016.0, i.cx, 1e-9)
        assertEquals(1512.0, i.cy, 1e-9)
        assertTrue(i.usable)
    }

    @Test
    fun intrinsicsFallBackTo70DegreeFov() {
        val i = IntrinsicsEstimator.estimate(null, 640, 480, null, null, null, 1.35)
        assertEquals(IntrinsicsEstimator.FROM_FOV, i.source)
        val f = 320.0 / tan(Math.toRadians(35.0))
        assertEquals(f, i.fx, 1e-9)
        assertEquals(f, i.fy, 1e-9)
        assertEquals(320.0, i.cx, 1e-9)
        assertEquals(240.0, i.cy, 1e-9)
        assertEquals(1.35, i.cameraHeightM, 0.0)
        assertTrue(i.usable)
    }

    @Test
    fun invalidCalibrationNeverLeaksZeros() {
        val i = IntrinsicsEstimator.estimate(doubleArrayOf(0.0, 0.0, 0.0, 0.0), 640, 480, null, null, null, 1.2)
        assertEquals(IntrinsicsEstimator.FROM_FOV, i.source)
        assertTrue(i.usable)
        assertTrue(i.fx > 0.0 && i.fy > 0.0)
    }

    @Test
    fun unknownSensorSizeIsFlaggedAsUnusable() {
        val i = IntrinsicsEstimator.estimate(null, 0, 0, null, null, null, 1.2)
        assertFalse(i.usable)
        assertEquals(0.0, obj(i.toJson(null)).num("analysis_width"), 0.0)
    }

    @Test
    fun cameraJsonMatchesPcLoaderContract() {
        val intr = IntrinsicsEstimator.estimate(null, 4032, 3024, 4.0, 5.76, 4.29, 1.2)
        val m = obj(intr.toJson(FrameGeometry(640, 480, 90)))
        for (key in listOf("fx", "fy", "cx", "cy")) assertTrue("$key must be a positive number", m.num(key) > 0.0)
        assertEquals(4032.0, m.num("width"), 0.0)
        assertEquals(3024.0, m.num("height"), 0.0)
        assertEquals(1.2, m.num("camera_height_m"), 1e-9)
        assertEquals(640.0, m.num("analysis_width"), 0.0)
        assertEquals(480.0, m.num("analysis_height"), 0.0)
        assertEquals(90.0, m.num("frame_rotation_deg"), 0.0)
        assertEquals(IntrinsicsEstimator.FROM_FOCAL_MM, m["intrinsics_source"])
        assertEquals("active_array", m["pixel_coordinates"])
    }

    @Test
    fun metaJsonRecordsStepsSensorsCameraAndClock() {
        val stats = SessionStats(
            sessionId = "session-2026-11-04T00-00-00Z-123",
            createdUtc = "2026-11-04T00:00:00Z",
            device = DeviceInfo("Xiaomi", "18 Pro", "device-x", 35, "arm64-v8a", "0.1.0"),
            fpsTarget = 8,
            cameraHeightM = 1.2,
            cameraId = "0",
            startElapsedNs = 1_000_000_000L,
            availableSensors = listOf("android.sensor.gravity", "android.sensor.step_detector"),
        )
        stats.hasStepDetector = true
        stats.stepPermissionGranted = true
        stats.intrinsicsSource = IntrinsicsEstimator.FROM_CALIBRATION
        stats.frames = 10
        stats.frameWidth = 640
        stats.frameHeight = 480
        stats.frameRotationDeg = 90
        stats.stepsDetected = 3
        stats.stepCounterStart = 100.0
        stats.stepCounterLast = 103.0
        stats.addSensorRow("android.sensor.gravity")
        stats.addSensorRow("android.sensor.gravity")
        stats.addSensorRow("android.sensor.step_detector")
        stats.endElapsedNs = 13_500_000_000L
        stats.endUtc = "2026-11-04T00:00:12Z"

        val m = obj(stats.toJson())
        assertEquals(1.0, m.num("schema_version"), 0.0)
        assertEquals("session-2026-11-04T00-00-00Z-123", m["session_id"])
        assertEquals(12.5, m.num("duration_s"), 1e-9)

        val clock = m.obj("clock")
        assertEquals(1_000_000_000.0, clock.num("start_elapsed_ns"), 0.0)
        assertEquals(13_500_000_000.0, clock.num("end_elapsed_ns"), 0.0)
        assertEquals("session_start", clock["steps_t_reference"])

        val capture = m.obj("capture")
        assertEquals(8.0, capture.num("fps_target"), 0.0)
        assertEquals(1.2, capture.num("camera_height_m"), 1e-9)
        assertEquals(10.0, capture.num("frames"), 0.0)
        assertEquals(640.0, capture.num("frame_width"), 0.0)
        assertEquals("camera.json", capture["intrinsics_file"])
        assertEquals(IntrinsicsEstimator.FROM_CALIBRATION, capture["intrinsics_source"])

        val sensors = m.obj("sensors")
        assertEquals(3.0, sensors.num("row_count"), 0.0)
        val byType = sensors.obj("rows_by_type")
        assertEquals(2.0, byType.num("android.sensor.gravity"), 0.0)
        assertEquals(1.0, byType.num("android.sensor.step_detector"), 0.0)

        val steps = m.obj("steps")
        assertEquals(true, steps["detector_available"])
        assertEquals(true, steps["activity_recognition_permission"])
        assertEquals(3.0, steps.num("detector_events"), 0.0)
        assertEquals(100.0, steps.num("counter_start"), 0.0)
        assertEquals(103.0, steps.num("counter_last"), 0.0)
        assertEquals(3.0, steps.num("counter_delta"), 0.0)
        assertEquals("steps.csv", steps["file"])
    }

    @Test
    fun metaJsonOmitsStepsFileWhenNoDetector() {
        val stats = SessionStats(
            sessionId = "s", createdUtc = "now",
            device = DeviceInfo("m", "m", "d", 26, null, null),
            fpsTarget = 8, cameraHeightM = 1.2, cameraId = null,
            startElapsedNs = 0L, availableSensors = listOf("android.sensor.gravity"),
        )
        val steps = obj(stats.toJson()).obj("steps")
        assertEquals(false, steps["detector_available"])
        assertEquals(false, steps["activity_recognition_permission"])
        assertTrue(MiniJson.isNull(steps["file"]))
        assertTrue(MiniJson.isNull(steps["counter_start"]))
        assertTrue(MiniJson.isNull(steps["counter_delta"]))
    }

    @Test
    fun metaJsonEscapesDeviceStrings() {
        val manufacturer = "Xiaomi \"Pro\" \\ Lab\nSouth"
        val stats = SessionStats(
            sessionId = "s", createdUtc = "now",
            device = DeviceInfo(manufacturer, "A\u0001B", "d", 35, "arm64-v8a", "0.1.0"),
            fpsTarget = 8, cameraHeightM = 1.2, cameraId = "0",
            startElapsedNs = 0L, availableSensors = emptyList(),
        )
        val device = obj(stats.toJson()).obj("device")
        assertEquals(manufacturer, device["manufacturer"])
        assertEquals("A\u0001B", device["model"])
    }

    @Test
    fun nv21RespectsUVInterleaving() {
        // 4x4: chroma is 2x2 and rows are V,U,V,U.
        val out = yuv420ToNv21(4, 4,
            ImagePlane(bb(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15), 4, 1),
            ImagePlane(bb(10, 11, 12, 13), 2, 1),
            ImagePlane(bb(20, 21, 22, 23), 2, 1))
        assertEquals(24, out.size)
        assertEquals((0..15).map { it.toByte() }, out.take(16))
        assertEquals(listOf(20, 10, 21, 11, 22, 12, 23, 13).map { it.toByte() }, out.drop(16))
    }

    @Test
    fun nv21SkipsYRowPadding() {
        val out = yuv420ToNv21(2, 2,
            ImagePlane(bb(1, 2, 99, 3, 4, 99), 3, 1),
            ImagePlane(bb(7), 1, 1),
            ImagePlane(bb(8), 1, 1))
        assertEquals(listOf(1, 2, 3, 4, 8, 7).map { it.toByte() }, out.toList())
    }

    @Test
    fun nv21SkipsUVPixelPadding() {
        // Chroma buffers with pixelStride 2 (common CameraX layout) must not read the padding.
        val out = yuv420ToNv21(2, 2,
            ImagePlane(bb(1, 2, 3, 4), 2, 1),
            ImagePlane(bb(7, 0), 2, 2),
            ImagePlane(bb(8, 0), 2, 2))
        assertEquals(listOf(1, 2, 3, 4, 8, 7).map { it.toByte() }, out.toList())
    }

    @Test
    fun nv21HandlesOddDimensionsAndShortBuffers() {
        val empty = ByteBuffer.wrap(ByteArray(0))
        val out = yuv420ToNv21(3, 3,
            ImagePlane(bb(1, 2, 3, 4, 5, 6, 7, 8, 9), 3, 1),
            ImagePlane(empty, 0, 0),
            ImagePlane(empty, 0, 0))
        assertEquals(9 + 2 * 2 * 2, out.size)
        assertEquals(listOf(1, 2, 3, 4, 5, 6, 7, 8, 9).map { it.toByte() }, out.take(9))
        assertTrue(out.drop(9).all { it == 128.toByte() })
    }

    @Test
    fun stepCsvUsesSessionRelativeMilliseconds() {
        assertEquals("t_ms\n", STEPS_HEADER)
        assertEquals("0.000\n", stepLine(1_000_000_000L, 1_000_000_000L))
        assertEquals("12.500\n", stepLine(1_012_500_000L, 1_000_000_000L))
        assertEquals("0.000\n", stepLine(999_000_000L, 1_000_000_000L))
        assertEquals(1.5, relativeMs(1_001_500_000L, 1_000_000_000L), 1e-9)
    }
}
