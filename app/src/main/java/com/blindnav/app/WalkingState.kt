package com.blindnav.app

import kotlin.math.sqrt

/**
 * Coarse walking/stationary signal from accelerometer variance.
 * Not a step counter and not a distance estimate; used only to keep
 * "camera pointed at a nearby surface" from becoming a walking hazard.
 */
class WalkingState(
    private val windowMs: Long = 1_500L,
    private val threshold: Double = 0.55,
    private val minSamples: Int = 10,
) {
    private val samples = ArrayDeque<Pair<Long, Double>>()
    var walking = false
        private set

    fun observeMagnitude(magnitude: Double, atMs: Long): Boolean {
        if (!magnitude.isFinite() || atMs < 0L) return walking
        samples.addLast(atMs to magnitude)
        while (samples.isNotEmpty() && atMs - samples.first().first > windowMs) samples.removeFirst()
        if (samples.size < minSamples) {
            walking = false
            return walking
        }
        val mean = samples.sumOf { it.second } / samples.size
        val variance = samples.sumOf { (it.second - mean) * (it.second - mean) } / samples.size
        walking = sqrt(variance) >= threshold
        return walking
    }

    fun reset() {
        samples.clear()
        walking = false
    }
}
