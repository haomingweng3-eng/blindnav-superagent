package com.blindnav.app.shadow

import android.content.Context
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.Image
import android.os.Build
import android.util.Log
import com.blindnav.app.ObstacleDetector
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Frame
import com.google.ar.core.ImageMetadata
import com.google.ar.core.SemanticLabel
import com.google.ar.core.Session
import com.google.ar.core.exceptions.CameraNotAvailableException
import com.google.ar.core.exceptions.DeadlineExceededException
import com.google.ar.core.exceptions.NotTrackingException
import com.google.ar.core.exceptions.NotYetAvailableException
import com.google.ar.core.exceptions.ResourceExhaustedException
import com.google.ar.core.exceptions.UnavailableApkTooOldException
import com.google.ar.core.exceptions.UnavailableArcoreNotInstalledException
import com.google.ar.core.exceptions.UnavailableDeviceNotCompatibleException
import com.google.ar.core.exceptions.UnavailableSdkTooOldException
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * ARCore 影子引擎（任务书第四/六/七/十一/十二/十四/十五/十七节）。
 *
 * 线程模型（本轮审计后明确固定下来，之前所有重活都压在 ARCore 线程上会污染它自己的延迟数）：
 *
 *   ARCore 线程：session.update() → 取 pose / depth / 未旋转 RGB → 投递给模型线程 → 记 FRAME/POSE/DEPTH。
 *   模型线程（单线程 + "只要最新帧"）：SegFormer、YOLO、遥测 binder 调用、overlay 落盘、取帧 JPEG。
 *
 * 模型跟不上相机时**丢弃旧帧**而不是排队（任务书 11.2）：用 AtomicReference 存最新待处理帧，
 * busy 时新帧替换旧帧、旧帧立即 recycle。
 *
 * 所有输出只写 [BlindNavSensorLogger] 与 [status]，**不接 TtsEngine，不产生播报**。
 */
class ArShadowEngine(
    private val context: Context,
    private val flags: ShadowFlags,
    private val logger: BlindNavSensorLogger,
    @Volatile private var displayRotation: Int,
    @Volatile private var viewportWidthPx: Int,
    @Volatile private var viewportHeightPx: Int
) {

    class Capability(
        val availability: String,
        val sessionCreated: Boolean,
        val arCoreVersion: String?,
        val depthAutomatic: Boolean,
        val depthRawOnly: Boolean,
        val depthModeUsed: String,
        val semanticEnabled: Boolean,
        val cameraConfigs: List<String>,
        val cameraTimestampSource: String,
        val failure: String?
    ) {
        fun json(): Map<String, Any?> = linkedMapOf(
            "arcore_availability" to availability,
            "arcore_session_created" to sessionCreated,
            "arcore_version" to arCoreVersion,
            "arcore_depth_automatic" to depthAutomatic,
            "arcore_depth_raw_only" to depthRawOnly,
            "arcore_depth_mode_used" to depthModeUsed,
            "arcore_semantic_enabled" to semanticEnabled,
            "arcore_camera_configs" to cameraConfigs.joinToString(" | "),
            "camera_timestamp_source" to cameraTimestampSource,
            "arcore_failure" to failure
        )
    }

    /** 交给模型线程的一帧：未旋转 RGB（所有权随对象转移，处理完必须 recycle）。 */
    private class ModelJob(
        val bitmap: android.graphics.Bitmap,
        val arNanos: Long,
        val receivedRt: Long,
        val generation: Long
    ) {
        fun recycle() = runCatching { bitmap.recycle() }
    }

    @Volatile private var running = false
    private var worker: Thread? = null
    private var session: Session? = null
    private var gl: EglOffscreenContext? = null
    private var detector: ObstacleDetector? = null

    private val clock = SensorClock()
    private val depthReader = DepthReader()
    private val depthJitter = DepthReader.JitterTracker()
    private val aligner = DepthRgbAligner()
    private val worldPath = WorldPathShadow()
    private val telemetry = SystemTelemetry(context)
    private val cpuSampler = SystemTelemetry.CpuSampler()
    private var traversability: TraversabilityShadowModel? = null
    private var overlay: ShadowOverlayRecorder? = null

    private val modelExecutor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "ArShadowModels").apply { isDaemon = true }
    }
    private val modelBusy = AtomicBoolean(false)
    private val pendingJob = AtomicReference<ModelJob?>(null)

    var capability: Capability? = null
        private set

    @Volatile
    var status: Map<String, Any?> = linkedMapOf("state" to "idle")
        private set

    @Volatile var lastError: String? = null
        private set

    /** 会话世代：每次 start() 递增，所有证据与日志都带它，跨会话结果不会拼在一起。 */
    private val generation = AtomicLong(0L)

    private var frames = 0L
    private var freshFrames = 0L
    private var framesWithImage = 0L
    private var framesWithDepth = 0L
    private var framesDepthNotYet = 0L
    private var framesTracking = 0L
    private var lastFrameTimestamp = -1L
    private var fpsWindowStart = 0L
    private var fpsWindowFrames = 0
    @Volatile private var measuredFps = 0.0
    private var lastTelemetryAt = 0L
    private var lastDepthMeters: FloatArray? = null
    private var lastDepthW = 0
    private var lastDepthH = 0
    private var lastDepthEvidence: WorldPathShadow.Evidence? = null
    private var unknownStreak = 0
    private var lastUnknownAt = 0L

    /** 最近一次 mask 与其来源帧；推理失败时**清空**而不是沿用旧 mask。 */
    private var lastMask: BooleanArray? = null
    private var lastMaskW = 0
    private var lastMaskH = 0
    private var lastMaskEvidence: WorldPathShadow.Evidence? = null
    private var lastMaskSource = "none"

    fun start(): String? {
        if (running) { Log.w(TAG, "ENGINE_START rejected=already_running"); return "already_running" }
        val ownership = ArCameraOwnership.probe()
        if (ownership != ArCameraOwnership.State.FREE) {
            Log.w(TAG, "ENGINE_START rejected=camera_held_by_${ownership.name.lowercase()}")
            return "camera_held_by_${ownership.name.lowercase()}"
        }
        if (!ArCameraOwnership.claimShadow()) {
            Log.w(TAG, "ENGINE_START rejected=shadow_claim_held_by_other_engine")
            return "shadow_already_running"
        }
        Log.w(TAG, "ENGINE_START accepted generation_pending=true")
        running = true
        generation.incrementAndGet()
        worker = Thread({ loop() }, "ArShadowEngine").apply { isDaemon = true; start() }
        return null
    }

    fun stop() {
        running = false
        runCatching { worker?.join(4000) }
        if (worker?.isAlive == true) {
            // worker 还活着就绝不能放 session 句柄丢掉，否则下一次 start() 会开第二个 session。
            lastError = "WORKER_DID_NOT_STOP"
            Log.w(TAG, "worker did not stop within 4s; refusing to clear session handle")
        }
        worker = null
        runCatching { modelExecutor.shutdownNow() }
        ArCameraOwnership.releaseShadow()
    }

    /** 方向变化：只改字段，真正的 setDisplayGeometry 由 ARCore 线程下一帧执行，避免与 update() 并发。 */
    fun updateViewport(rotation: Int, widthPx: Int, heightPx: Int) {
        displayRotation = rotation
        viewportWidthPx = widthPx
        viewportHeightPx = heightPx
        viewportDirty = true
    }

    @Volatile private var viewportDirty = false

    private fun loop() {
        val offscreen = EglOffscreenContext()
        gl = offscreen
        val glError = offscreen.create()
        Log.w(TAG, "EGL_CREATE result=${glError ?: "ok"} texture_id=${offscreen.textureId}")
        if (glError != null) { fail("EGL_CREATE_FAILED:$glError"); cleanup(); return }
        val current = offscreen.makeCurrent()
        Log.w(TAG, "EGL_MAKE_CURRENT result=${if (current) "ok" else "failed"}")
        if (!current) { fail("EGL_MAKE_CURRENT_FAILED"); cleanup(); return }
        val created = createSession() ?: run { cleanup(); return }
        session = created
        if (!configure(created)) { cleanup(); return }
        created.setCameraTextureName(offscreen.textureId)
        created.setDisplayGeometry(displayRotation, viewportWidthPx, viewportHeightPx)
        if (!resumeWithRetry(created)) { cleanup(); return }
        runSelfTest(created)
        logger.log("SESSION", linkedMapOf(
            "event" to "start",
            "generation" to generation.get(),
            "flags" to flags.summary(),
            "device_model" to Build.MODEL,
            "android_sdk" to Build.VERSION.SDK_INT,
            "camera_ids" to cameraIds()
        ) + capabilityJson() + clock.snapshot() + aligner.json())

        if (flags.traversabilityExperiment) {
            val model = TraversabilityShadowModel(context)
            traversability = model
            logger.log("INFERENCE", linkedMapOf(
                "model" to "segformer_cityscapes", "event" to "load",
                "available" to model.available, "path" to model.modelPath,
                "input_size" to model.inputSize, "declared_output" to model.declaredOutput(),
                "reason" to model.unavailableReason
            ))
            if (!model.available) lastError = model.unavailableReason
        }
        if (flags.safeMotionShadow && detector == null) {
            detector = runCatching { ObstacleDetector(context) }.getOrNull()
            logger.log("INFERENCE", linkedMapOf(
                "model" to "yolov8n", "event" to "load", "available" to (detector != null)
            ))
        }
        overlay = ShadowOverlayRecorder(context, logger)
        status = linkedMapOf("state" to "running") + capabilityJson()
        runLoop()
        cleanup()
    }

    /** resume() 抢相机失败是常见竞态（CameraX 刚释放），必须退避重试而不是直接放弃。 */
    private fun resumeWithRetry(created: Session): Boolean {
        var attempt = 0
        while (running && attempt < RESUME_ATTEMPTS) {
            try {
                created.resume()
                Log.w(TAG, "ARCORE_RESUME=OK attempts=${attempt + 1}")
                if (attempt > 0) {
                    logger.log("SESSION", linkedMapOf("event" to "resume_recovered", "attempts" to attempt + 1))
                }
                return true
            } catch (e: CameraNotAvailableException) {
                attempt += 1
                Log.w(TAG, "ARCORE_RESUME_RETRY attempt=$attempt exception=${e.javaClass.name}")
                logger.log("SESSION", linkedMapOf(
                    "event" to "resume_retry", "attempt" to attempt, "error" to describe(e)
                ))
                sleep(200L * attempt)
            } catch (e: Throwable) {
                Log.w(TAG, "ARCORE_RESUME=FAILED exception=${e.javaClass.name} message=${e.message}")
                fail("SESSION_RESUME_FAILED:${describe(e)}")
                return false
            }
        }
        Log.w(TAG, "ARCORE_RESUME=FAILED reason=camera_busy_after_$RESUME_ATTEMPTS" +
            "_attempts exception=CameraNotAvailableException")
        fail("CAMERA_BUSY_AFTER_${RESUME_ATTEMPTS}_RESUME_ATTEMPTS")
        return false
    }

    private fun runLoop() {
        var consecutiveErrors = 0
        while (running) {
            val sess = session ?: break
            if (viewportDirty) {
                viewportDirty = false
                runCatching { sess.setDisplayGeometry(displayRotation, viewportWidthPx, viewportHeightPx) }
            }
            val frame: Frame = try {
                val contextOk = gl?.makeCurrent() == true
                if (!contextOk) { fail("EGL_CONTEXT_LOST"); break }
                sess.update()
            } catch (e: CameraNotAvailableException) {
                logger.log("SESSION", linkedMapOf("event" to "camera_not_available", "error" to describe(e)))
                if (++consecutiveErrors > MAX_CONSECUTIVE_ERRORS) { fail("CAMERA_LOST"); break }
                sleep(50); continue
            } catch (e: Throwable) {
                logger.log("SESSION", linkedMapOf("event" to "update_failed", "error" to describe(e)))
                if (++consecutiveErrors > MAX_CONSECUTIVE_ERRORS) { fail("UPDATE_FAILED:${describe(e)}"); break }
                sleep(50); continue
            }
            consecutiveErrors = 0
            frames += 1
            val receivedRt = clock.nowRt()
            try {
                process(frame, receivedRt)
            } catch (e: Throwable) {
                logger.log("SESSION", linkedMapOf("event" to "frame_error", "error" to describe(e)))
            }
        }
        logger.log("SESSION", linkedMapOf(
            "event" to "stop", "generation" to generation.get(),
            "frames" to frames, "fresh_frames" to freshFrames,
            "frames_with_image" to framesWithImage, "frames_with_depth" to framesWithDepth,
            "frames_depth_not_yet" to framesDepthNotYet, "frames_tracking" to framesTracking,
            "fps" to "%.2f".format(measuredFps).toDouble(), "last_error" to lastError
        ))
    }

    private fun process(frame: Frame, receivedRt: Long) {
        val arTimestamp = frame.timestamp
        if (arTimestamp == 0L) return            // 启动期空帧，官方 Session.update() 注释说明
        val duplicate = arTimestamp == lastFrameTimestamp
        lastFrameTimestamp = arTimestamp
        if (!duplicate) {
            freshFrames += 1
            fpsWindowFrames += 1                // 只统计真正的新帧，重复帧不能虚增 FPS
            if (receivedRt - fpsWindowStart >= 1_000_000_000L) {
                measuredFps = fpsWindowFrames * 1_000_000_000.0 / (receivedRt - fpsWindowStart)
                fpsWindowStart = receivedRt
                fpsWindowFrames = 0
            }
        }
        val gen = generation.get()
        val cameraTimestamp = runCatching { frame.androidCameraTimestamp }.getOrDefault(0L)
        clock.observe(arTimestamp, cameraTimestamp)

        val camera = frame.camera
        val pose = ArPoseSample.of(arTimestamp, cameraTimestamp, receivedRt, camera, camera.pose, clock)
        if (pose.trackingState == TRACKING) framesTracking += 1
        lastPoseTracking = pose.trackingState == TRACKING

        val fields = LinkedHashMap<String, Any?>()
        fields["generation"] = gen
        fields["frame"] = frames
        fields["ar_ns"] = arTimestamp
        fields["cam_ns"] = cameraTimestamp
        fields["rt_ns"] = receivedRt
        fields["mono_ns"] = clock.nowMono()
        fields["duplicate"] = duplicate
        fields["ar_fps"] = "%.2f".format(measuredFps).toDouble()
        fields.putAll(clock.snapshot())
        // 位姿字段无条件并进 FRAME：POSE 事件在 frameSampleEvery=1 时不会单独落，
        // 而"Pose 稳不稳 / 拿歪会不会误纠偏"全部依赖 tx/ty/tz/q*/yaw 这条连续序列。
        fields.putAll(pose.json())

        // ---- RGB（未旋转，几何路径专用）----
        var bitmap: android.graphics.Bitmap? = null
        val imageStart = clock.nowMono()
        var rgb: Image? = null
        try {
            rgb = frame.acquireCameraImage()
            framesWithImage += 1
            val converted = YuvConverter.toBitmap(rgb, 0)   // 0 = 不旋转
            bitmap = converted.bitmap
            fields["rgb_w"] = converted.width
            fields["rgb_h"] = converted.height
            fields["rgb_format"] = formatName(rgb.format)
            fields["rgb_row_stride_y"] = converted.rowStrideY
            fields["rgb_row_stride_uv"] = converted.rowStrideUv
            fields["rgb_pixel_stride_uv"] = converted.pixelStrideUv
            fields["rgb_truncated"] = converted.truncated
            fields["rgb_luma_mean"] = converted.lumaMean
            fields["rgb_rotation_deg"] = 0
            fields["rgb_acquire_convert_us"] = (clock.nowMono() - imageStart) / 1000.0
            logger.latency("rgb_acquire_convert").add(clock.nowMono() - imageStart)
            aligner.noteImageSize(converted.width, converted.height)
            fields.putAll(metadataOf(frame))
        } catch (e: NotYetAvailableException) {
            fields["rgb_available"] = false
        } catch (e: ResourceExhaustedException) {
            fields["rgb_error"] = "RESOURCE_EXHAUSTED"
        } catch (e: DeadlineExceededException) {
            fields["rgb_error"] = "DEADLINE_EXCEEDED"
        } catch (e: Throwable) {
            fields["rgb_error"] = describe(e)
        } finally {
            runCatching { rgb?.close() }
        }

        // ---- Depth ----
        readDepth(frame, pose, arTimestamp, fields)

        // ---- Scene Semantics：只在 SegFormer 缺席时作为 coarse 可通行性来源 ----
        if (flags.traversabilityExperiment && traversability?.available != true) {
            readSemantics(frame, arTimestamp, fields)
        }

        // ---- 单帧取图（最后十米采集）：拷贝后交给调用方，绝不在 ARCore 线程做 JPEG 压缩 ----
        val pendingCapture = captureRequest
        if (pendingCapture != null && bitmap != null && !duplicate) {
            captureRequest = null
            val copy = runCatching { bitmap.copy(android.graphics.Bitmap.Config.ARGB_8888, false) }.getOrNull()
            if (copy != null) {
                val poseCopy = pose
                runCatching { modelExecutor.execute { pendingCapture(copy, poseCopy) } }
                    .onFailure { copy.recycle() }
            }
        }

        // ---- 模型工作投递（只要最新帧，不排队）----
        if (bitmap != null && (flags.traversabilityExperiment || flags.safeMotionShadow)) {
            val job = ModelJob(bitmap, arTimestamp, receivedRt, gen)
            val displaced = pendingJob.getAndSet(job)
            displaced?.recycle()
            if (modelBusy.compareAndSet(false, true)) {
                val queued = pendingJob.getAndSet(null)
                if (queued == null) modelBusy.set(false) else runCatching {
                    modelExecutor.execute { runModels(queued) }
                }.onFailure { queued.recycle(); modelBusy.set(false) }
            } else {
                fields["model_dropped_frames"] = (fields["model_dropped_frames"] as? Long ?: 0L) + 1L
            }
        } else {
            runCatching { bitmap?.recycle() }
        }

        // ---- WorldPath 影子（用最近一次 mask + 本帧深度/位姿，各自带新鲜度）----
        if (flags.worldPathShadow) {
            val dm = lastDepthMeters
            val de = lastDepthEvidence
            val m = lastMask
            val me = lastMaskEvidence
            if (dm != null && de != null && m != null && me != null && lastDepthW > 0) {
                val wpStart = clock.nowMono()
                val atMs = receivedRt / 1_000_000L
                val observed = worldPath.observe(dm, lastDepthW, lastDepthH, de,
                    m, lastMaskW, lastMaskH, me, aligner, pose, camera.pose, arTimestamp, atMs)
                logger.latency("worldpath").add(clock.nowMono() - wpStart)
                val decision = worldPath.shadowDecision(pose, atMs)
                fields["wp_mask_source"] = lastMaskSource
                if (observed != null) {
                    fields["wp_points"] = observed.points
                    fields["wp_forward_median_m"] = "%.3f".format(observed.forwardMedian).toDouble()
                    fields["wp_forward_p90_m"] = "%.3f".format(observed.forwardP90).toDouble()
                    fields["wp_lateral_spread_m"] = "%.3f".format(observed.lateralSpread).toDouble()
                    fields["wp_health"] = "%.3f".format(observed.health).toDouble()
                    if (observed.rejected != null) fields["wp_evidence_rejected"] = observed.rejected
                } else {
                    fields["wp_points"] = 0
                }
                fields.putAll(decision)
                if (!duplicate) logger.log("SHADOW", linkedMapOf(
                    "policy" to "worldpath", "generation" to gen, "frame" to frames,
                    "ar_ns" to arTimestamp, "rt_ns" to receivedRt,
                    "source_frame_timestamp" to arTimestamp, "result_timestamp" to clock.nowRt(),
                    "mask_source_frame" to me.sourceArNanos, "depth_source_frame" to de.sourceArNanos,
                    "mask_age_ms" to ((arTimestamp - me.sourceArNanos) / 1_000_000L),
                    "depth_age_ms" to ((arTimestamp - de.sourceArNanos) / 1_000_000L)
                ) + decision + linkedMapOf(
                    "wp_evidence_rejected" to (observed?.rejected ?: "not_evaluated")
                ))
            } else {
                fields["wp_evidence_missing"] = true
            }
        }

        // ---- 资源遥测：投递到模型线程，不在 ARCore 线程做 binder 调用 ----
        if (receivedRt - lastTelemetryAt >= 1_000_000_000L) {
            lastTelemetryAt = receivedRt
            runCatching { modelExecutor.execute { telemetryTick() } }
        }

        if (frames % flags.frameSampleEvery == 0L) {
            fields["state"] = "running"
            status = LinkedHashMap(fields)
            logger.log("FRAME", LinkedHashMap(fields) + aligner.json())
        }
        // POSE 每帧无条件写：位姿抖动与静止判定需要连续序列，1/N 采样会打出洞。
        if (!duplicate) logger.log("POSE", pose.json() + linkedMapOf("generation" to gen))
    }

    private fun readDepth(frame: Frame, pose: ArPoseSample, arTimestamp: Long, fields: LinkedHashMap<String, Any?>) {
        val cap = capability
        if (cap == null || cap.depthModeUsed == "DISABLED") {
            fields["depth_available"] = false
            fields["depth_unavailable_reason"] = if (cap == null) "NO_CAPABILITY" else "DEVICE_UNSUPPORTED"
            return
        }
        if (pose.trackingState != TRACKING) {
            fields["depth_available"] = false
            fields["depth_unavailable_reason"] = "NOT_TRACKING"
            return
        }
        val depthStart = clock.nowMono()
        var full: Image? = null
        var raw: Image? = null
        var conf: Image? = null
        var metersForPath: FloatArray? = null
        var wForPath = 0
        var hForPath = 0
        try {
            full = frame.acquireDepthImage16Bits()
            wForPath = full.width; hForPath = full.height
            val read = depthReader.read(full, null)
            metersForPath = read.first
            fields.putAll(read.second.json(arTimestamp))
            logger.latency("depth_read").add(clock.nowMono() - depthStart)
            framesWithDepth += 1
        } catch (e: NotYetAvailableException) {
            framesDepthNotYet += 1
            fields["depth_available"] = false
            fields["depth_unavailable_reason"] = "NOT_YET_AVAILABLE"
        } catch (e: NotTrackingException) {
            fields["depth_available"] = false
            fields["depth_unavailable_reason"] = "NOT_TRACKING"
        } catch (e: ResourceExhaustedException) {
            fields["depth_error"] = "RESOURCE_EXHAUSTED"
        } catch (e: IllegalStateException) {
            fields["depth_error"] = "DEPTH_MODE_NOT_ENABLED"
        } catch (e: Throwable) {
            fields["depth_error"] = describe(e)
        } finally {
            runCatching { full?.close() }
        }
        try {
            raw = frame.acquireRawDepthImage16Bits()
            conf = frame.acquireRawDepthConfidenceImage()
            val rawRead = depthReader.read(raw, conf)
            fields["raw_depth_coverage"] = "%.4f".format(rawRead.second.coverage).toDouble()
            fields["raw_depth_confident_coverage"] = "%.4f".format(rawRead.second.confidentCoverage).toDouble()
            fields["raw_depth_mean_confidence"] = "%.3f".format(rawRead.second.meanConfidence).toDouble()
            fields["raw_depth_conf_hist"] = rawRead.second.confidenceHistogram.joinToString(",")
            fields["raw_depth_nearband_median_m"] = "%.3f".format(rawRead.second.nearBandConfidentMedianMeters).toDouble()
            fields["raw_depth_max_mm_seen"] = rawRead.second.maxMmSeen
            fields["raw_depth_px_beyond_8191mm"] = rawRead.second.beyond8191mm
            fields["raw_depth_conf_size_matches"] = rawRead.second.confidenceSizeMatches
            fields["raw_depth_ts_ns"] = rawRead.second.imageTimestampNanos
            // 背投影用带置信度的 raw 版本；置信度图尺寸不符时退回 full depth 并记录。
            if (rawRead.second.confidenceSizeMatches && rawRead.second.confidentPixels > 0) {
                metersForPath = rawRead.first
                wForPath = rawRead.second.width
                hForPath = rawRead.second.height
            }
        } catch (e: Throwable) {
            fields["raw_depth_error"] = describe(e)
        } finally {
            runCatching { raw?.close() }
            runCatching { conf?.close() }
        }
        if (metersForPath != null && wForPath > 0) {
            aligner.noteDepthSize(wForPath, hForPath)
            lastDepthMeters = metersForPath
            lastDepthW = wForPath
            lastDepthH = hForPath
            lastDepthEvidence = WorldPathShadow.Evidence(
                sourceArNanos = arTimestamp, receivedRtNanos = clock.nowRt(),
                generation = generation.get(), label = "depth"
            )
            depthJitter.observe(metersForPath, wForPath, hForPath,
                pose.tx, pose.ty, pose.tz, pose.qw, pose.qx, pose.qy, pose.qz)
            fields.putAll(depthJitter.json())
            fields["depth_ms"] = (clock.nowMono() - depthStart) / 1_000_000L
        }
    }

    /**
     * ARCore Scene Semantics 作为**备用** coarse 可通行性来源。
     *
     * 定位要说清楚：1.57 的标签里 SIDEWALK 含 associated curbs 但**不提供独立路缘边界**，
     * 也没有 crosswalk/curb 类，所以它只能回答"这块像人行道还是像车行道"，
     * **不能**用来判断"离路缘还有多远"或替代 SegFormer。它只在 SegFormer 资产缺席时顶上，
     * 且 mask 来源会如实记成 arcore_scene_semantics，分析脚本据此分桶。
     *
     * 必须在 ARCore 线程调用（`acquireSemanticImage()` 绑定当前 frame）。
     * 语义图宽 256、高按显示宽高比，与 CPU image 的宽高比可能不同 → 记 mismatch 标志。
     */
    private fun readSemantics(frame: Frame, arTimestamp: Long, fields: LinkedHashMap<String, Any?>) {
        if (capability?.semanticEnabled != true) {
            fields["sem_available"] = false
            return
        }
        var semantic: Image? = null
        var semConf: Image? = null
        try {
            semantic = frame.acquireSemanticImage()
            semConf = runCatching { frame.acquireSemanticConfidenceImage() }.getOrNull()
            val output = SemanticMask.read(semantic) { id ->
                SemanticLabel.values().getOrNull(id)?.name ?: "OUT_OF_RANGE_$id"
            }
            if (output != null) {
                lastMask = output.traversable
                lastMaskW = output.gridWidth
                lastMaskH = output.gridHeight
                lastMaskEvidence = WorldPathShadow.Evidence(
                    sourceArNanos = arTimestamp, receivedRtNanos = clock.nowRt(),
                    generation = generation.get(), label = "semantics"
                )
                lastMaskSource = "arcore_scene_semantics"
                fields.putAll(output.json())
                val semAspect = output.sourceWidth.toDouble() / output.sourceHeight.coerceAtLeast(1)
                val rgbAspect = fields["rgb_w"]?.toString()?.toDoubleOrNull()?.let { rw ->
                    val rh = (fields["rgb_h"] as? Int)?.toDouble() ?: 0.0
                    if (rh > 0) rw / rh else 0.0
                } ?: 0.0
                fields["sem_aspect_mismatch"] =
                    rgbAspect > 0 && semAspect > 0 && kotlin.math.abs(semAspect - rgbAspect) / rgbAspect > 0.03
            }
            fields["sem_road_fraction"] = fraction(frame, SemanticLabel.ROAD)
            fields["sem_sidewalk_fraction"] = fraction(frame, SemanticLabel.SIDEWALK)
            fields["sem_person_fraction"] = fraction(frame, SemanticLabel.PERSON)
            fields["sem_vehicle_fraction"] = fraction(frame, SemanticLabel.VEHICLE)
            fields["sem_object_fraction"] = fraction(frame, SemanticLabel.OBJECT)
        } catch (e: NotYetAvailableException) {
            fields["sem_available"] = false
            fields["sem_unavailable_reason"] = "NOT_YET_AVAILABLE"
        } catch (e: Throwable) {
            fields["sem_error"] = describe(e)
            // 语义失败同样不能沿用上一帧 mask。
            lastMask = null
            lastMaskEvidence = null
            lastMaskSource = "semantics_failed"
        } finally {
            runCatching { semantic?.close() }
            runCatching { semConf?.close() }
        }
    }

    private fun fraction(frame: Frame, label: SemanticLabel): Double? =
        runCatching { frame.getSemanticLabelFraction(label).toDouble() / 100.0 }.getOrNull()

    /** 模型线程：分割 → mask（带来源帧）；YOLO → 障碍（带来源帧）；overlay 落盘。 */
    private fun runModels(job: ModelJob) {
        try {
            val model = traversability
            if (flags.traversabilityExperiment && model != null && model.available) {
                val output = model.detect(job.bitmap)
                if (output != null) {
                    lastMask = output.traversable
                    lastMaskW = output.gridWidth
                    lastMaskH = output.gridHeight
                    lastMaskEvidence = WorldPathShadow.Evidence(
                        sourceArNanos = job.arNanos, receivedRtNanos = job.receivedRt,
                        generation = job.generation, label = "segformer"
                    )
                    lastMaskSource = "segformer"
                    logger.latency("segmentation")
                        .add(output.preprocessNanos + output.inferenceNanos + output.postprocessNanos)
                    logger.log("INFERENCE", linkedMapOf(
                        "model" to "segformer_cityscapes", "generation" to job.generation,
                        "source_frame_timestamp" to job.arNanos,
                        "result_timestamp" to clock.nowRt()
                    ) + output.json())
                    overlay?.record(job.bitmap, output, lastDepthMeters, lastDepthW, lastDepthH)
                } else {
                    // 推理失败必须清空，不能沿用上一帧 mask 装作这一帧有证据。
                    lastMask = null
                    lastMaskEvidence = null
                    lastMaskSource = "segformer_failed"
                    logger.log("INFERENCE", linkedMapOf(
                        "model" to "segformer_cityscapes", "generation" to job.generation,
                        "event" to "run_failed", "reason" to model.unavailableReason,
                        "source_frame_timestamp" to job.arNanos
                    ))
                }
            }
            if (flags.safeMotionShadow) {
                val det = detector
                if (det != null) {
                    val start = System.nanoTime()
                    val rotated = runCatching {
                        YuvConverter.rotate(job.bitmap, rotationDegreesOf(displayRotation))
                    }.getOrNull() ?: job.bitmap
                    val hits = runCatching { det.detect(rotated) }.getOrNull()
                    val elapsed = System.nanoTime() - start
                    if (rotated !== job.bitmap) runCatching { rotated.recycle() }
                    logger.latency("detector").add(elapsed)
                    val nearBand = nearBandMedian(lastDepthMeters, lastDepthW, lastDepthH)
                    val decision = safeMotionShadow(hits, nearBand, start, job)
                    logger.log("INFERENCE", linkedMapOf(
                        "model" to "yolov8n", "generation" to job.generation,
                        "source_frame_timestamp" to job.arNanos,
                        "result_timestamp" to clock.nowRt(),
                        "det_us" to elapsed / 1000.0,
                        "det_hits" to (hits?.size ?: -1),
                        "det_names" to hits?.joinToString(",") { it.name + it.side }
                    ))
                    logger.log("SHADOW", linkedMapOf(
                        "policy" to "safe_motion", "generation" to job.generation,
                        "ar_ns" to job.arNanos, "rt_ns" to job.receivedRt,
                        "source_frame_timestamp" to job.arNanos, "result_timestamp" to clock.nowRt()
                    ) + decision)
                }
            }
        } catch (e: Throwable) {
            Log.w(TAG, "model thread: ${describe(e)}")
        } finally {
            job.recycle()
            modelBusy.set(false)
        }
    }

    private fun telemetryTick() {
        runCatching {
            val tele = LinkedHashMap(telemetry.snapshot())
            tele["fps"] = "%.2f".format(measuredFps).toDouble()
            tele["cpu_pct"] = cpuSampler.sample()?.let { "%.1f".format(it).toDouble() }
            tele["frames"] = frames
            tele["generation"] = generation.get()
            logger.log("TELEMETRY", tele)
            status = LinkedHashMap(status).apply { putAll(tele) }
        }
    }

    /**
     * Safe Motion Authority 影子（任务书第十四/十六节）。
     *
     * 证据新鲜度用**同域** ARCore 时间戳差；跨域不可比时不猜数，直接标 unknown 并退 REOBSERVE。
     */
    private fun safeMotionShadow(
        hits: List<ObstacleDetector.Hit>?,
        nearBandMeters: Double?,
        inferenceStartMono: Long,
        job: ModelJob
    ): Map<String, Any?> {
        val out = LinkedHashMap<String, Any?>()
        val detectorRisk = !hits.isNullOrEmpty()
        val depthRisk = nearBandMeters != null && nearBandMeters > 0.0 && nearBandMeters <= NEAR_BAND_MAX_M
        val corridorOccupied = corridorOccupancy(lastMask, lastMaskW, lastMaskH)
        val poseFresh = lastPoseTracking
        out["sma_detector_risk"] = detectorRisk
        out["sma_depth_risk"] = depthRisk
        out["sma_nearband_depth_m"] = nearBandMeters?.let { "%.3f".format(it).toDouble() }
        out["sma_corridor_occupied"] = corridorOccupied?.let { "%.3f".format(it).toDouble() }
        out["sma_traversability_source"] = lastMaskSource
        out["sma_semantics_curb_unavailable"] = true   // Scene Semantics 无独立 curb 边界，见任务书第七点
        out["sma_pose_fresh"] = poseFresh
        val unknown = !detectorRisk && depthRisk && corridorOccupied != null && corridorOccupied > 0.5
        val now = System.currentTimeMillis()
        if (unknown) {
            unknownStreak = if (now - lastUnknownAt < UNKNOWN_HOLD_MS) unknownStreak + 1 else 1
            lastUnknownAt = now
        } else if (now - lastUnknownAt > UNKNOWN_HOLD_MS) {
            unknownStreak = 0
        }
        out["unknown_obstacle_candidate"] = unknown
        out["unknown_obstacle_streak"] = unknownStreak
        // 证据龄：mask/depth 与本次 detector 输入帧是否同一帧，用同域 ar_ns 判定。
        val maskAgeMs = lastMaskEvidence?.let { (job.arNanos - it.sourceArNanos) / 1_000_000L }
        out["sma_mask_age_ms"] = maskAgeMs
        out["sma_inference_us"] = (System.nanoTime() - inferenceStartMono) / 1000.0
        val stale = !poseFresh || (maskAgeMs != null && (maskAgeMs > MAX_EVIDENCE_AGE_MS || maskAgeMs < 0))
        out["sma_evidence_stale"] = stale
        val wouldStop = detectorRisk || (depthRisk && unknownStreak >= UNKNOWN_CONFIRM_FRAMES)
        out["sma_would_stop"] = wouldStop
        out["sma_would_have_said"] = when {
            stale -> "REOBSERVE"
            wouldStop -> "STOP"
            else -> "KEEP"
        }
        out["sma_shadow_only"] = true
        return out
    }

    @Volatile private var lastPoseTracking = false

    private fun corridorOccupancy(mask: BooleanArray?, w: Int, h: Int): Double? {
        if (mask == null || w <= 0 || h <= 0) return null
        var total = 0
        var blocked = 0
        for (y in (h * DepthReader.NEAR_BAND_FROM).toInt() until h) {
            for (x in (w * DepthReader.NEAR_BAND_LEFT).toInt() until (w * DepthReader.NEAR_BAND_RIGHT).toInt()) {
                total++
                if (!mask[y * w + x]) blocked++
            }
        }
        return if (total == 0) null else blocked.toDouble() / total
    }

    private fun nearBandMedian(meters: FloatArray?, w: Int, h: Int): Double? {
        if (meters == null || w <= 0 || h <= 0 || meters.size < w * h) return null
        val values = ArrayList<Float>()
        for (y in (h * DepthReader.NEAR_BAND_FROM).toInt() until h) {
            for (x in (w * DepthReader.NEAR_BAND_LEFT).toInt() until (w * DepthReader.NEAR_BAND_RIGHT).toInt()) {
                val v = meters[y * w + x]
                if (!v.isNaN() && v > 0f) values.add(v)
            }
        }
        if (values.isEmpty()) return null
        values.sort()
        return values[values.size / 2].toDouble()
    }

    private fun metadataOf(frame: Frame): Map<String, Any?> {
        val meta = runCatching { frame.imageMetadata }.getOrNull() ?: return emptyMap()
        val out = LinkedHashMap<String, Any?>()
        runCatching { out["iso"] = meta.getInt(ImageMetadata.SENSOR_SENSITIVITY) }
            .onFailure { out["iso_read_failed"] = describe(it) }
        // SENSOR_EXPOSURE_TIME 与 SENSOR_FRAME_DURATION 都是 int64：ARCore ImageMetadata 没有
        // getLong，只能 getByteArray 后按小端 8 字节自己拼。用 getInt 会抛 MetadataNotFoundException。
        runCatching { out["exposure_ns"] = int64(meta, ImageMetadata.SENSOR_EXPOSURE_TIME) }
            .onFailure { out["exposure_read_failed"] = describe(it) }
        runCatching { out["frame_duration_ns"] = int64(meta, ImageMetadata.SENSOR_FRAME_DURATION) }
            .onFailure { out["frame_duration_read_failed"] = describe(it) }
        return out
    }

    private fun int64(meta: ImageMetadata, key: Int): Long {
        val bytes = meta.getByteArray(key)
        require(bytes.size >= 8) { "expected 8 bytes, got ${bytes.size}" }
        var v = 0L
        for (i in 7 downTo 0) v = (v shl 8) or (bytes[i].toLong() and 0xFF)
        return v
    }

    private fun createSession(): Session? {
        val availability = runCatching { ArCoreApk.getInstance().checkAvailability(context) }.getOrNull()
        val version = runCatching {
            context.packageManager.getPackageInfo("com.google.ar.core", 0).versionName
        }.getOrNull()
        val availabilityName = availability?.name ?: "CHECK_FAILED"
        val tsSource = cameraTimestampSource()
        if (availability == null || !availability.isSupported) {
            Log.w(TAG, "ARCORE_AVAILABILITY_ENGINE_VIEW value=$availabilityName " +
                "isSupported=${availability?.isSupported} -> 不创建 Session")
            capability = Capability(availabilityName, false, version, false, false, "DISABLED", false,
                emptyList(), tsSource, "ARCORE_NOT_SUPPORTED")
            fail("ARCORE_NOT_SUPPORTED:$availabilityName")
            logger.log("SESSION", linkedMapOf("event" to "capability") + capabilityJson())
            return null
        }
        val created = try {
            val s = Session(context)
            Log.w(TAG, "ARCORE_SESSION_CREATE=SUCCESS class=Session")
            s
        } catch (e: Throwable) {
            Log.w(TAG, "ARCORE_SESSION_CREATE=FAILED exception=${e.javaClass.name} message=${e.message}")
            val reason = when (e) {
                is UnavailableArcoreNotInstalledException -> "ARCORE_NOT_INSTALLED"
                is UnavailableApkTooOldException -> "ARCORE_APK_TOO_OLD"
                is UnavailableSdkTooOldException -> "ARCORE_SDK_TOO_OLD"
                is UnavailableDeviceNotCompatibleException -> "DEVICE_NOT_COMPATIBLE"
                else -> describe(e)
            }
            Log.w(TAG, "ARCORE_SESSION_CREATE_REASON=$reason")
            capability = Capability(availabilityName, false, version, false, false, "DISABLED", false,
                emptyList(), tsSource, "SESSION_CREATE:$reason")
            fail("SESSION_CREATE:$reason")
            logger.log("SESSION", linkedMapOf("event" to "capability") + capabilityJson())
            return null
        }
        val depthAutomatic = runCatching { created.isDepthModeSupported(Config.DepthMode.AUTOMATIC) }
            .getOrDefault(false)
        val depthRaw = runCatching { created.isDepthModeSupported(Config.DepthMode.RAW_DEPTH_ONLY) }
            .getOrDefault(false)
        val semantic = runCatching { created.isSemanticModeSupported(Config.SemanticMode.ENABLED) }
            .getOrDefault(false)
        Log.w(TAG, "ARCORE_DEPTH_AUTOMATIC=$depthAutomatic ARCORE_DEPTH_RAW_ONLY=$depthRaw " +
            "ARCORE_SEMANTIC_ENABLED=$semantic")
        val configs = runCatching {
            // CameraConfigFilter 默认会滤掉带深度传感器的配置，必须显式放开，否则"Raw Depth 支持"会被漏记。
            val filter = com.google.ar.core.CameraConfigFilter(created)
                .setDepthSensorUsage(java.util.EnumSet.allOf(com.google.ar.core.CameraConfig.DepthSensorUsage::class.java))
                .setStereoCameraUsage(java.util.EnumSet.allOf(com.google.ar.core.CameraConfig.StereoCameraUsage::class.java))
                .setFacingDirection(com.google.ar.core.CameraConfig.FacingDirection.BACK)
            created.getSupportedCameraConfigs(filter).map {
                "tex=${it.textureSize.width}x${it.textureSize.height} img=${it.imageSize.width}x${it.imageSize.height}" +
                    " fps=${it.fpsRange} depth=${it.depthSensorUsage} stereo=${it.stereoCameraUsage} id=${it.cameraId}"
            }
        }.getOrElse {
            runCatching { created.supportedCameraConfigs.map { "tex=${it.textureSize.width}x${it.textureSize.height}" } }
                .getOrDefault(emptyList())
        }
        capability = Capability(availabilityName, true, version, depthAutomatic, depthRaw,
            if (depthAutomatic) "AUTOMATIC" else if (depthRaw) "RAW_DEPTH_ONLY" else "DISABLED",
            semantic, configs, tsSource, null)
        clock.setCameraTimeSource(tsSource)
        logger.log("SESSION", linkedMapOf(
            "event" to "capability", "generation" to generation.get(),
            "device_model" to Build.MODEL, "android_sdk" to Build.VERSION.SDK_INT,
            "camera_ids" to cameraIds()
        ) + capabilityJson())
        return created
    }

    /** @return false 表示配置失败，调用方必须终止，不能带着 depthMode=DISABLED 继续测深度。 */
    private fun configure(session: Session): Boolean {
        val config = Config(session)
        config.updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
        config.focusMode = Config.FocusMode.AUTO
        config.planeFindingMode = Config.PlaneFindingMode.DISABLED
        config.lightEstimationMode = Config.LightEstimationMode.DISABLED
        config.instantPlacementMode = Config.InstantPlacementMode.DISABLED
        config.augmentedFaceMode = Config.AugmentedFaceMode.DISABLED
        val cap = capability
        config.depthMode = when {
            cap?.depthAutomatic == true -> Config.DepthMode.AUTOMATIC
            cap?.depthRawOnly == true -> Config.DepthMode.RAW_DEPTH_ONLY
            else -> Config.DepthMode.DISABLED
        }
        if (cap?.semanticEnabled == true) config.semanticMode = Config.SemanticMode.ENABLED
        val result = runCatching { session.configure(config) }
        Log.w(TAG, "ARCORE_CONFIGURE result=${if (result.isSuccess) "ok" else "failed"} " +
            "depth_mode=${config.depthMode} semantic=${config.semanticMode} " +
            "exception=${result.exceptionOrNull()?.javaClass?.name}")
        if (result.isFailure) {
            // 带期望深度模式却配置失败时不能静默继续，否则整场深度数据都是 DEPTH_MODE_NOT_ENABLED。
            fail("CONFIGURE_FAILED:${describe(result.exceptionOrNull() ?: Throwable("unknown"))}")
            return false
        }
        return true
    }

    private fun cleanup() {
        // 顺序：先停 ARCore（pause→close），再关模型，最后销毁 GL；每步独立 runCatching 保证都执行。
        runCatching { session?.pause() }
        runCatching { session?.close() }
        session = null
        pendingJob.getAndSet(null)?.recycle()
        runCatching { detector?.close() }
        detector = null
        runCatching { traversability?.close() }
        traversability = null
        overlay = null
        runCatching { gl?.destroy() }
        gl = null
        worldPath.reset()
        depthJitter.reset()
        clock.reset()
        lastMask = null; lastMaskEvidence = null; lastDepthMeters = null; lastDepthEvidence = null
        status = linkedMapOf("state" to "stopped", "error" to lastError, "frames" to frames)
    }

    private fun capabilityJson(): Map<String, Any?> = capability?.json() ?: emptyMap()

    private fun cameraIds(): String = runCatching {
        cameraManager().cameraIdList.joinToString(",")
    }.getOrDefault("unavailable")

    /**
     * 读后置相机的 `SENSOR_INFO_TIMESTAMP_SOURCE`。
     * ARCore 不直接暴露这个特性，但 Camera2 可以查；它决定 cam_ns 能不能和 elapsedRealtime 相减。
     */
    private fun cameraTimestampSource(): String = runCatching {
        val manager = cameraManager()
        val id = manager.cameraIdList.firstOrNull { cid ->
            val facing = manager.getCameraCharacteristics(cid)
                .get(CameraCharacteristics.LENS_FACING)
            facing == CameraCharacteristics.LENS_FACING_BACK
        } ?: return@runCatching SensorClock.CameraTimeSource.NOT_READ.name
        val source = manager.getCameraCharacteristics(id)
            .get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE)
        when (source) {
            CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE_REALTIME ->
                SensorClock.CameraTimeSource.REALTIME.name
            CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE_UNKNOWN ->
                SensorClock.CameraTimeSource.MONOTONIC_UNKNOWN_BASE.name
            else -> SensorClock.CameraTimeSource.NOT_READ.name
        }
    }.getOrDefault(SensorClock.CameraTimeSource.NOT_READ.name)

    private fun cameraManager(): CameraManager =
        context.getSystemService(Context.CAMERA_SERVICE) as CameraManager

    private fun fail(reason: String) {
        lastError = reason
        Log.w(TAG, reason)
        logger.log("SESSION", linkedMapOf("event" to "fatal", "reason" to reason, "generation" to generation.get()))
    }

    private fun sleep(ms: Long) = runCatching { Thread.sleep(ms) }

    private fun rotationDegreesOf(rotation: Int): Int = when (rotation) {
        0 -> 90; 1 -> 0; 2 -> 270; else -> 180
    }

    private fun describe(e: Throwable?): String =
        "${e?.javaClass?.simpleName}:${e?.message?.take(160) ?: "no-message"}"

    private fun formatName(format: Int): String = when (format) {
        ImageFormat.YUV_420_888 -> "YUV_420_888"
        else -> "format_$format"
    }

    /** 几何/坐标约定的合成自检：启动后跑一次，结果写日志（PASS/FAIL + 具体数值）。 */
    private fun runSelfTest(created: Session) {
        val results = runCatching { GeometrySelfTest.run() }
            .getOrElse { listOf("SELF_TEST_UNAVAILABLE:${describe(it)}") }
        logger.log("SELFTEST", linkedMapOf(
            "generation" to generation.get(),
            "checks" to results.joinToString(" | ")
        ))
        status = LinkedHashMap(status).apply { put("self_test", results.joinToString(" | ")) }
    }

    companion object {
        private const val TAG = "BlindNavShadow"
        private const val TRACKING = "TRACKING"
        private const val MAX_CONSECUTIVE_ERRORS = 60
        private const val RESUME_ATTEMPTS = 10
        const val MAX_EVIDENCE_AGE_MS = 400L
        const val UNKNOWN_CONFIRM_FRAMES = 3
        const val UNKNOWN_HOLD_MS = 1500L
        const val NEAR_BAND_MAX_M = 1.2
    }

    /** 单帧取图回调；在模型线程上执行，收到的 bitmap 是副本，调用方可自由持有。 */
    @Volatile
    var captureRequest: ((android.graphics.Bitmap, ArPoseSample) -> Unit)? = null
}
