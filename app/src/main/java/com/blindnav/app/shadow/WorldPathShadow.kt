package com.blindnav.app.shadow

import com.blindnav.app.WorldPathMemory
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.min

/**
 * WorldPath 手机影子（任务书第十二节）。
 *
 * 这是 `WorldPathMemory` 第一次真正接到实时数据上——电脑阶段它只有回放脚本喂，
 * 生产代码里没有任何调用点（见 ANDROID_VISUAL_PIPELINE_CURRENT.md 第 4 节）。
 *
 * 几何约定（与 ARCore 1.57 官方注释一致，已核对）：
 *  - world frame：Y 向上、重力对齐、米、右手系；相机初始位置为原点。
 *  - camera frame：**X 右、Y 上、Z 向后，相机看向 -Z**。
 *    `Camera.getImageIntrinsics()` 返回的是**未旋转 CPU image** 的内参，
 *    所以背投影必须用未旋转图像像素，且 `v` 向下增长 → camY 取负、camZ = -d。
 *  - 相机系→世界系只用 ARCore 自己的 `Pose.transformPoint()`，不自写四元数。
 *  - 存进 `WorldPathMemory` 的 2D 点是世界系 `(worldX, -worldZ)`；
 *    但"前方多远/横向偏多少"这类**门限**必须在**相机朝向系**里算，
 *    否则人转身 90° 后，"前方 0.3–5m"筛出来的是侧向点。
 *
 * 证据新鲜度：mask、depth、pose 各自带 `source_ar_ns`；任一路超过 [maxEvidenceAgeMs]
 * 就整帧判 UNKNOWN 并记原因，**不**把 Frame N 的 mask 和 Frame N+k 的深度拼成"健康 WorldPath"。
 *
 * 本类**只产生日志字段**：lateral/heading error、path health、stale、would_have_said。
 * 不返回任何会被 TTS 消费的东西。
 */
class WorldPathShadow(
    private val memory: WorldPathMemory = WorldPathMemory(maxAgeMs = 1500L),
    private val maxForwardMeters: Double = 5.0,
    private val minForwardMeters: Double = 0.3,
    private val maxEvidenceAgeMs: Long = 400L
) {

    /** 一路证据的来源帧与到达时刻。generation 用来识别跨会话/跨重启的串台。 */
    data class Evidence(
        val sourceArNanos: Long,
        val receivedRtNanos: Long,
        val generation: Long,
        val label: String
    )

    class Frame(
        val points: Int,
        val forwardMedian: Double,
        val forwardP90: Double,
        val lateralSpread: Double,
        val health: Double,
        val rejected: String?
    )

    private var lastPose: DoubleArray? = null
    private var lastAtMs: Long = -1
    private var velocity = Pair(0.0, 0.0)
    private var recentFrames = 0
    private var recentUsable = 0
    private var lastGoodObservationAtMs = -1L
    private var sessionGeneration = -1L

    /** 本帧真正参与几何计算的深度像素 (u, v, d)，供 Debug Overlay 与离线核对。 */
    @Volatile var lastUsedPixels: List<DoubleArray> = emptyList()
        private set

    /** 与 lastUsedPixels 一一对应的世界点，用于把中心线画回画面。 */
    @Volatile var lastWorldPoints: List<DoubleArray> = emptyList()
        private set

    /** 闭环重投影最大误差（像素）。非 0 就说明背投影/内参链上有实现层错误。 */
    @Volatile var lastReprojectMaxPx: Double = 0.0
        private set

    fun reset() {
        memory.reset(); lastPose = null; lastAtMs = -1; velocity = Pair(0.0, 0.0)
        recentFrames = 0; recentUsable = 0; lastGoodObservationAtMs = -1L; sessionGeneration = -1L
    }

    /**
     * @param meters    深度图（米，NaN 无效），尺寸 [depthW] x [depthH]，**未旋转**坐标系
     * @param mask      可通行网格（行优先 [maskW] x [maskH]），由**未旋转** RGB 帧推理得到
     * @param pose      当前帧位姿样本（含未旋转 CPU image 的内参）
     * @param cameraPose 同一帧的 ARCore 相机位姿，用于相机系→世界系
     * @param depthEvidence / maskEvidence 两路证据各自的来源帧，用于新鲜度判定
     */
    fun observe(
        meters: FloatArray, depthW: Int, depthH: Int, depthEvidence: Evidence,
        mask: BooleanArray, maskW: Int, maskH: Int, maskEvidence: Evidence,
        align: DepthRgbAligner,
        pose: ArPoseSample, cameraPose: com.google.ar.core.Pose,
        currentArNanos: Long, atMs: Long
    ): Frame? {
        if (pose.trackingState != "TRACKING") { recentFrames++; return null }
        val fx = pose.focalX ?: return null
        val fy = pose.focalY ?: return null
        val cx = pose.principalX ?: return null
        val cy = pose.principalY ?: return null
        if (fx <= 0f || fy <= 0f || pose.imageWidth <= 0 || pose.imageHeight <= 0) return null

        // 会话/世代变了就丢掉旧路径，绝不把上一次会话的折线接到这一次上。
        if (sessionGeneration != maskEvidence.generation) {
            memory.reset(); recentFrames = 0; recentUsable = 0
            sessionGeneration = maskEvidence.generation
        }

        val maskAge = currentArNanos - maskEvidence.sourceArNanos
        val depthAge = currentArNanos - depthEvidence.sourceArNanos
        if (maskAge !in 0..maxEvidenceAgeMs * 1_000_000L) {
            return Frame(0, 0.0, 0.0, 0.0, 0.0, "stale_mask_${maskAge / 1_000_000}ms")
        }
        if (depthAge !in 0..maxEvidenceAgeMs * 1_000_000L) {
            return Frame(0, 0.0, 0.0, 0.0, 0.0, "stale_depth_${depthAge / 1_000_000}ms")
        }

        recentFrames += 1
        val camera = doubleArrayOf(pose.tx, pose.ty, pose.tz)
        // 相机朝向系的两个世界基向量：forward = 相机 -Z，right = 相机 +X。
        val forwardVec = cameraPose.rotateVector(floatArrayOf(0f, 0f, -1f))
        val rightVec = cameraPose.rotateVector(floatArrayOf(1f, 0f, 0f))
        val inverse = cameraPose.inverse()

        val points = ArrayList<WorldPathMemory.Point>()
        val forwards = ArrayList<Double>()
        val laterals = ArrayList<Double>()
        val usedPixels = ArrayList<DoubleArray>()
        val worldPoints = ArrayList<DoubleArray>()
        var reprojectMaxPx = 0.0
        val stepX = maxOf(1, depthW / 8)
        val stepY = maxOf(1, depthH / 6)
        for (dy in 0 until depthH step stepY) {
            // 只用画面下半部分：上半天空/建筑立面反投影出来的"地面点"是假的。
            if (dy < depthH / 2) continue
            for (dx in 0 until depthW step stepX) {
                val d = meters.getOrNull(dy * depthW + dx) ?: continue
                if (d.isNaN() || d <= 0f) continue
                // 前方门限用**相机系距离**，不用世界 -Z。
                if (d.toDouble() < minForwardMeters || d.toDouble() > maxForwardMeters) continue
                val image = align.depthToImage(dx, dy, depthW, depthH, pose.imageWidth, pose.imageHeight)
                val u = image.first
                val v = image.second
                val mu = ((u / pose.imageWidth) * maskW).toInt().coerceIn(0, maskW - 1)
                val mv = ((v / pose.imageHeight) * maskH).toInt().coerceIn(0, maskH - 1)
                if (!mask[mv * maskW + mu]) continue
                // ARCore 相机系：X 右、Y 上、Z 向后；像素 v 向下 → camY 取负；深度沿视线为正 → camZ = -d。
                val cam = pixelToCamera(u, v, d.toDouble(), fx.toDouble(), fy.toDouble(),
                    cx.toDouble(), cy.toDouble())
                val world = cameraPose.transformPoint(floatArrayOf(cam[0].toFloat(), cam[1].toFloat(), cam[2].toFloat()))
                usedPixels.add(doubleArrayOf(u, v, d.toDouble()))
                val groundOffset = world[1] - pose.ty          // 相对相机高度的垂直分量
                if (groundOffset > 0.2 || groundOffset < -2.5) continue   // 不是"相机下方的地面"
                val ox = world[0].toDouble() - camera[0]
                val oz = world[2].toDouble() - camera[2]
                // 投影到相机朝向系：这才是"前方多远 / 横向偏多少"。
                val forwardDistance = ox * forwardVec[0].toDouble() + oz * forwardVec[2].toDouble()
                val lateralDistance = ox * rightVec[0].toDouble() + oz * rightVec[2].toDouble()
                if (forwardDistance <= 0.0) continue
                points.add(WorldPathMemory.Point(world[0].toDouble(), (-world[2]).toDouble()))
                forwards.add(forwardDistance)
                laterals.add(lateralDistance)
                worldPoints.add(doubleArrayOf(world[0].toDouble(), world[1].toDouble(), world[2].toDouble()))
                // 闭环自检：世界点再投影回像素，应与原像素重合。
                // 这只抓实现层的符号/顺序错（不会抓"两个约定本来就一致"的错），
                // 但它是唯一不需要人看屏幕就能发现的几何回归。
                val back = inverse.transformPoint(world)
                if (back[2] < -1e-3f) {
                    val reprojected = cameraToPixel(
                        back[0].toDouble(), back[1].toDouble(), (-back[2]).toDouble(),
                        fx.toDouble(), fy.toDouble(), cx.toDouble(), cy.toDouble()
                    )
                    val err = hypot(reprojected[0] - u, reprojected[1] - v)
                    if (err > reprojectMaxPx) reprojectMaxPx = err
                }
            }
        }
        if (points.size >= 2) {
            recentUsable += 1
            lastGoodObservationAtMs = atMs
        }
        memory.observe(points, atMs)
        // 供 Debug Overlay 画：本帧真正用到的像素、世界点、以及闭环重投影误差。
        lastUsedPixels = usedPixels
        lastWorldPoints = worldPoints
        lastReprojectMaxPx = reprojectMaxPx

        val sorted = forwards.sorted()
        return Frame(
            points = points.size,
            forwardMedian = if (sorted.isEmpty()) 0.0 else sorted[sorted.size / 2],
            forwardP90 = if (sorted.isEmpty()) 0.0 else sorted[min(sorted.size - 1, (sorted.size * 0.9).toInt())],
            lateralSpread = if (laterals.size < 2) 0.0
            else ((laterals.maxOrNull() ?: 0.0) - (laterals.minOrNull() ?: 0.0)).coerceAtLeast(0.0),
            health = if (recentFrames == 0) 0.0 else recentUsable.toDouble() / recentFrames,
            rejected = null
        )
    }

    /**
     * 影子决策：算出"如果正式运行会说什么"，只写日志。
     *
     * 2D 坐标约定与 [observe] 存点时一致：`(worldX, -worldZ)`，速度也是同一约定，
     * 所以 `WorldPathMemory.control()` 里的横向/朝向误差才有意义。
     */
    fun shadowDecision(pose: ArPoseSample, atMs: Long): Map<String, Any?> {
        // 先算"距离上一次可用观测过了多久"，再更新，否则这个数恒为 0。
        val staleMs = if (lastGoodObservationAtMs > 0) atMs - lastGoodObservationAtMs else -1L
        if (pose.trackingState != "TRACKING") {
            memory.reset()
            return linkedMapOf(
                "wp_state" to "tracking_paused",
                "wp_action" to "REOBSERVE",
                "wp_would_have_said" to "REOBSERVE",
                "wp_would_stop" to true,
                "wp_stale_ms" to staleMs
            )
        }
        updateVelocity(doubleArrayOf(pose.tx, pose.ty, pose.tz), atMs)
        val control = memory.control(pose.tx, -pose.tz, velocity.first, velocity.second, atMs)
        val speed = hypot(velocity.first, velocity.second)
        val headingError = if (speed > 0.15) Math.toDegrees(atan2(velocity.second, velocity.first)) else Double.NaN
        return linkedMapOf(
            "wp_action" to control.action,
            "wp_safe" to control.safe,
            "wp_confidence" to "%.3f".format(control.confidence).toDouble(),
            "wp_lateral_error_m" to if (control.lateralError.isFinite())
                "%.3f".format(control.lateralError).toDouble() else null,
            "wp_speed_mps" to "%.2f".format(speed).toDouble(),
            "wp_heading_deg" to if (headingError.isFinite()) "%.1f".format(headingError).toDouble() else null,
            "wp_stale_ms" to staleMs,
            "wp_reproject_max_px" to "%.3f".format(lastReprojectMaxPx).toDouble(),
            "wp_points_used" to lastUsedPixels.size,
            // 影子模式核心字段：真跑起来会说什么，但这一版**不会**说出口。
            "wp_would_have_said" to control.action.uppercase(),
            "wp_would_stop" to (control.action == "stop" || !control.safe)
        )
    }

    private fun updateVelocity(camera: DoubleArray, atMs: Long) {
        val previous = lastPose
        val dtMs = if (lastAtMs > 0) atMs - lastAtMs else 0
        if (previous != null && dtMs in 50..2000) {
            val dt = dtMs / 1000.0
            velocity = Pair((camera[0] - previous[0]) / dt, -(camera[2] - previous[2]) / dt)
        }
        lastPose = camera.copyOf()
        lastAtMs = atMs
    }

    companion object {
        /**
         * 像素 + 沿视线深度 → ARCore 相机系点（X 右、Y 上、Z 向后）。
         * 单独抽出来是为了让 GeometrySelfTest 测的是**生产同一条公式**，不是测试自己抄的一份。
         */
        fun pixelToCamera(u: Double, v: Double, depthMeters: Double,
                          fx: Double, fy: Double, cx: Double, cy: Double): DoubleArray = doubleArrayOf(
            (u - cx) * depthMeters / fx,
            -(v - cy) * depthMeters / fy,
            -depthMeters
        )

        /** 相机系点 → 像素，pixelToCamera 的逆。 */
        fun cameraToPixel(camX: Double, camY: Double, depthForward: Double,
                          fx: Double, fy: Double, cx: Double, cy: Double): DoubleArray = doubleArrayOf(
            cx + camX * fx / depthForward,
            cy - camY * fy / depthForward
        )

        /** 与电脑阶段一致的报告口径：只报"到中心线的距离分布"，不报成安全距离。 */
        fun summarizeDistance(values: List<Double>): String {
            if (values.isEmpty()) return "n=0"
            val s = values.sorted()
            return "n=${s.size} median=%.3fm p90=%.3fm".format(s[s.size / 2], s[min(s.size - 1, (s.size * 0.9).toInt())])
        }
    }
}

/**
 * 深度像素 ↔ 彩色图像像素映射。
 *
 * ARCore 深度图与 CPU 彩色图**分辨率不同**（典型 160x120 vs 640x480），官方只承诺
 * "对齐到相机图像"，没承诺两者像素网格等比；而 1.57 的 `Coordinates2d` 里已经没有
 * `DEPTH_TEXTURE_UV`（只剩 IMAGE_PIXELS / TEXTURE_NORMALIZED / VIEW 等），
 * `Coordinates3d` 也只有两个 EIS 值，没有 WORLD_XYZ。
 *
 * 所以这里做的是**显式声明的假设**而不是隐式默认：归一化坐标线性映射，
 * 并且把宽高比是否一致、残差有没有量出来一并落盘。宽高比不一致时
 * [residualPixels] 会给出一个可解释的下界误差（不是 0），提示 WorldPath 几何需要重测。
 */
class DepthRgbAligner {
    var method: String = "normalized_linear"
        private set
    var depthAspect: Double = 0.0
        private set
    var imageAspect: Double = 0.0
        private set
    var aspectMismatch: Boolean = false
        private set
    /** 归一化映射在宽高比不一致时的最大像素偏移（沿短边方向），单位像素。 */
    var residualPixels: Double = 0.0
        private set

    fun noteDepthSize(w: Int, h: Int) { depthAspect = if (h > 0) w.toDouble() / h else 0.0 }

    fun noteImageSize(w: Int, h: Int) {
        imageAspect = if (h > 0) w.toDouble() / h else 0.0
        if (depthAspect > 0.0 && imageAspect > 0.0) {
            val ratio = depthAspect / imageAspect
            aspectMismatch = ratio < 0.97 || ratio > 1.03
            // 假设两图中心对齐、各自铺满视场：短边方向的边缘偏移即最大误差下界。
            residualPixels = if (aspectMismatch) (h * (1.0 - min(1.0, ratio)) / 2.0) else 0.0
        }
    }

    fun depthToImage(dx: Int, dy: Int, depthW: Int, depthH: Int, imageW: Int, imageH: Int): Pair<Double, Double> {
        val u = (dx + 0.5) / depthW * imageW
        val v = (dy + 0.5) / depthH * imageH
        return Pair(u.coerceIn(0.0, (imageW - 1).toDouble()), v.coerceIn(0.0, (imageH - 1).toDouble()))
    }

    fun json() = linkedMapOf<String, Any?>(
        "depth_rgb_align" to method,
        "depth_aspect" to if (depthAspect > 0) "%.4f".format(depthAspect).toDouble() else null,
        "image_aspect" to if (imageAspect > 0) "%.4f".format(imageAspect).toDouble() else null,
        "depth_rgb_aspect_mismatch" to aspectMismatch,
        "depth_rgb_align_residual_px" to if (residualPixels > 0) "%.2f".format(residualPixels).toDouble() else null
    )
}
