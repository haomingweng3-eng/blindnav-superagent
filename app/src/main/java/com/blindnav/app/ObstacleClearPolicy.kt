package com.blindnav.app

/**
 * Prevents a single detector dropout from clearing an active obstacle stop.
 * A clear state is accepted only after a small run of fresh, close-in-time
 * clear frames. This is temporal stability, not distance estimation.
 */
class ObstacleClearPolicy(
    private val requiredClearFrames: Int = 2,
    private val clearWindowMs: Long = 800L,
) {
    data class State(val blocking: Boolean, val clear: Boolean, val clearFrames: Int)

    private var blocking = false
    private var clearFrames = 0
    private var lastSampleAt = Long.MIN_VALUE

    fun observe(hasObstacle: Boolean, nowMs: Long): State {
        if (hasObstacle) {
            blocking = true
            clearFrames = 0
            lastSampleAt = nowMs
            return State(blocking = true, clear = false, clearFrames = 0)
        }
        if (!blocking) return State(blocking = false, clear = true, clearFrames = 0)
        val close = lastSampleAt != Long.MIN_VALUE && nowMs >= lastSampleAt &&
            nowMs - lastSampleAt <= clearWindowMs
        clearFrames = if (close) clearFrames + 1 else 1
        lastSampleAt = nowMs
        if (clearFrames >= requiredClearFrames) {
            blocking = false
            clearFrames = 0
            return State(blocking = false, clear = true, clearFrames = 0)
        }
        return State(blocking = true, clear = false, clearFrames = clearFrames)
    }

    fun reset() {
        blocking = false
        clearFrames = 0
        lastSampleAt = Long.MIN_VALUE
    }
}
