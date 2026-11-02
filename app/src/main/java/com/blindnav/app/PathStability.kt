package com.blindnav.app

/**
 * Temporal gate for the visual walkable-path signal. A single frame can be
 * pointed away from the path or contain a transient segmentation error, so it
 * is never enough to authorize a direction cue by itself.
 */
class PathStability(private val maxGapMs: Long = 1500L) {
    data class State(val stableFrames: Int, val side: String, val curve: String, val score: Double)

    private var key = ""
    private var count = 0
    private var lastAt = Long.MIN_VALUE

    fun reset() {
        key = ""
        count = 0
        lastAt = Long.MIN_VALUE
    }

    fun observe(side: String?, curve: String?, crosswalk: Boolean, score: Double, atMs: Long): State {
        val valid = side in setOf("left", "center", "right") &&
            curve in setOf("left", "none", "right") &&
            !crosswalk && score.isFinite() && score >= 0.6 && atMs >= 0
        if (!valid || (lastAt != Long.MIN_VALUE && (atMs < lastAt || atMs - lastAt > maxGapMs))) {
            reset()
            return State(0, "unknown", "none", 0.0)
        }
        val nextKey = "$side/$curve"
        count = if (nextKey == key) (count + 1).coerceAtMost(8) else 1
        key = nextKey
        lastAt = atMs
        return State(count, side!!, curve!!, score)
    }
}
