package com.blindnav.app

/** 用拍摄会话和单调时钟丢弃跨暂停、跨重开及推理过慢的旧结果。 */
class CameraFrameGate(private val clock: () -> Long = { System.nanoTime() }) {
    data class Stamp(val generation: Long, val atNanos: Long,
                     val timeSource: String = "analysis_received", val sensorTimestampNanos: Long = 0L)
    @Volatile var active = false
        private set
    private var generation = 0L
    private var sessionStartedAt = Long.MIN_VALUE

    @Synchronized fun start() { generation++; sessionStartedAt=clock(); active = true }
    @Synchronized fun stop() { generation++; active = false }
    @Synchronized fun capture(): Stamp? = if (active) Stamp(generation, clock()) else null
    @Synchronized fun captureFrame(sensorTimestampNanos: Long, realtime: Boolean,
                                   elapsedNanos: Long, nowNanos: Long): Stamp? {
        if (!active) return null
        if (nowNanos<sessionStartedAt) return null
        if (!realtime) return Stamp(generation,nowNanos,"analysis_received",sensorTimestampNanos)
        if (sensorTimestampNanos<=0 || sensorTimestampNanos>elapsedNanos) return null
        val age=elapsedNanos-sensorTimestampNanos
        if (age>3_000_000_000L) return null
        val at=nowNanos-age
        if (at<sessionStartedAt) return null
        return Stamp(generation,at,"sensor_realtime",sensorTimestampNanos)
    }
    @Synchronized fun isCurrent(stamp: Stamp, nowNanos: Long = clock()): Boolean =
        active && stamp.generation == generation && nowNanos - stamp.atNanos in 0L..3_000_000_000L
}
