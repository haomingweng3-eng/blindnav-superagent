package com.blindnav.app.shadow

/**
 * 时间基准登记与换算（任务书第七节）。详见 TIMESTAMP_AUDIT.md。
 *
 * 这台链路上实际存在四个时间基准，**没有任何一个官方承诺与另一个同基**：
 *
 *  1. `Frame.getTimestamp()`（ar_ns）：ARCore 官方注释原文——"The time base of this value is
 *     specifically **not defined**, but it is likely similar to `System.nanoTime()`"。
 *     也就是说"和 nanoTime 差不多"只是猜测，不能拿来跨域相减。
 *  2. `Frame.getAndroidCameraTimestamp()` / `Image.getTimestamp()`（cam_ns）：Camera2
 *     `SENSOR_TIMESTAMP`。它是否等于 `elapsedRealtimeNanos()` **取决于该相机的
 *     `SENSOR_INFO_TIMESTAMP_SOURCE` 是不是 REALTIME**；UNKNOWN 时它是 CLOCK_MONOTONIC 基，
 *     与 elapsedRealtime 相差一个"累计休眠时长"的常量。
 *  3. `SystemClock.elapsedRealtimeNanos()`（rt_ns）：单调、含休眠，跨域比较的唯一安全主基准。
 *  4. `System.nanoTime()`（mono_ns）：进程内耗时测量用，不可跨设备/跨会话比较。
 *
 * 因此本类的设计是：
 *  - **同域相减才给数**：帧新鲜度一律用 ar_ns 与 ar_ns 比、或 rt_ns 与 rt_ns 比。
 *  - **跨域只做拟合与报告，不做判据**：`cam_ns - ar_ns` 的均值与波动作为观测量落盘，
 *    用来回答"这台机器的两个时钟能不能桥接"，而不是当作通过/失败闸门。
 *  - 只有当 `SENSOR_INFO_TIMESTAMP_SOURCE == REALTIME` 被**实际读到**时，才允许
 *    [arToRt] 给出换算值；否则返回 null，让上层显式走"跨域不可比"分支。
 */
class SensorClock {

    enum class CameraTimeSource { REALTIME, MONOTONIC_UNKNOWN_BASE, NOT_READ }

    @Volatile var cameraTimeSource: CameraTimeSource = CameraTimeSource.NOT_READ
        private set

    /** 由引擎从 Camera2 `SENSOR_INFO_TIMESTAMP_SOURCE` 实际读到的值注入，不猜。 */
    fun setCameraTimeSource(name: String) {
        cameraTimeSource = runCatching { CameraTimeSource.valueOf(name) }
            .getOrDefault(CameraTimeSource.NOT_READ)
    }

    @Volatile private var offsetNanos: Long? = null

    @Volatile private var samples: Int = 0

    @Volatile private var deltaMin: Long = Long.MAX_VALUE

    @Volatile private var deltaMax: Long = Long.MIN_VALUE

    /** 用一帧的 (ar_ns, cam_ns) 更新 ar→cam 差的观测。cam_ns 缺失时跳过。 */
    fun observe(arTimestampNanos: Long, androidCameraTimestampNanos: Long) {
        if (arTimestampNanos <= 0L || androidCameraTimestampNanos <= 0L) return
        val candidate = androidCameraTimestampNanos - arTimestampNanos
        val current = offsetNanos
        if (current == null) {
            offsetNanos = candidate
            deltaMin = candidate
            deltaMax = candidate
            samples = 1
            return
        }
        samples += 1
        if (candidate < deltaMin) deltaMin = candidate
        if (candidate > deltaMax) deltaMax = candidate
        // 缓慢跟踪，权重固定；漂移本身要报告出来，不靠"重置"掩盖。
        offsetNanos = current + (candidate - current) / 8
    }

    /** 跨域换算：只有确认相机时间戳是 REALTIME 基才给值。 */
    fun arToRt(arTimestampNanos: Long): Long? {
        if (cameraTimeSource != CameraTimeSource.REALTIME) return null
        val off = offsetNanos ?: return null
        return arTimestampNanos + off
    }

    /** 同域帧龄：两个 ARCore 时间戳相减。这是唯一不需要任何跨域假设的新鲜度。 */
    fun frameAgeNanos(sourceArNanos: Long, currentArNanos: Long): Long? =
        if (sourceArNanos > 0L && currentArNanos >= sourceArNanos) currentArNanos - sourceArNanos else null

    /** 同域墙内耗时龄：两个 elapsedRealtime 时间戳相减。 */
    fun rtAgeNanos(sourceRtNanos: Long, nowRtNanos: Long): Long? =
        if (sourceRtNanos > 0L && nowRtNanos >= sourceRtNanos) nowRtNanos - sourceRtNanos else null

    fun nowRt(): Long = android.os.SystemClock.elapsedRealtimeNanos()

    fun nowMono(): Long = System.nanoTime()

    fun snapshot(): Map<String, Any?> {
        val spread = if (samples == 0) null else deltaMax - deltaMin
        return linkedMapOf(
            "camera_ts_source" to cameraTimeSource.name,
            "ar_cam_delta_ns" to offsetNanos,
            "ar_cam_delta_min_ns" to if (samples == 0) null else deltaMin,
            "ar_cam_delta_max_ns" to if (samples == 0) null else deltaMax,
            "ar_cam_delta_spread_ns" to spread,
            "ar_cam_delta_samples" to samples,
            // 明确写出来：跨域换算是否可用，由 sensor 特性决定，不由本类假设。
            "cross_domain_comparable" to (cameraTimeSource == CameraTimeSource.REALTIME && offsetNanos != null)
        )
    }

    fun reset() {
        offsetNanos = null
        samples = 0
        deltaMin = Long.MAX_VALUE
        deltaMax = Long.MIN_VALUE
    }
}
