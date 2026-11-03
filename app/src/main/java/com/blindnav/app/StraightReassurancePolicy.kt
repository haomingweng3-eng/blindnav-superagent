package com.blindnav.app

/** Low-frequency confirmation for a stable straight segment. */
class StraightReassurancePolicy(private val intervalMeters: Double = 10.0) {
    private var lastRemaining: Double? = null

    fun reset() { lastRemaining = null }

    fun shouldSpeak(remainingMeters: Double, eligible: Boolean, straightSegment: Boolean): Boolean {
        if (!remainingMeters.isFinite() || remainingMeters < 0.0) return false
        if (!eligible || !straightSegment) {
            lastRemaining = remainingMeters
            return false
        }
        val previous = lastRemaining
        if (previous == null || remainingMeters > previous + 3.0) {
            lastRemaining = remainingMeters
            return false
        }
        if (previous - remainingMeters < intervalMeters) return false
        lastRemaining = remainingMeters
        return true
    }
}
