package com.blindnav.app

import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * Hardware-neutral adapter for a stateful world-space path.
 *
 * Project Guideline obtains these points from ARCore pose + segmentation. This
 * class deliberately accepts points from any provider, so the desktop replay
 * can exercise the control and STOP policy without claiming ARCore is active.
 */
class WorldPathMemory(private val maxAgeMs: Long = 1500L) {
    data class Point(val x: Double, val y: Double)
    data class Control(val action: String, val safe: Boolean, val confidence: Double, val lateralError: Double)

    private val path = ArrayList<Point>()
    private var lastAt = Long.MIN_VALUE

    fun reset() { path.clear(); lastAt = Long.MIN_VALUE }

    fun observe(points: List<Point>, atMs: Long) {
        if (points.size < 2 || atMs < 0) return
        if (lastAt != Long.MIN_VALUE && atMs < lastAt) { reset() }
        // Keep a compact polyline; nearby repeated projections do not add noise.
        for (p in points) {
            if (p.x.isFinite() && p.y.isFinite() && (path.lastOrNull()?.let { hypot(it.x - p.x, it.y - p.y) } ?: 1.0) > 0.12) {
                path.add(p)
            }
        }
        if (path.size > 240) path.subList(0, path.size - 240).clear()
        lastAt = atMs
    }

    fun control(x: Double, y: Double, vx: Double, vy: Double, atMs: Long): Control {
        if (path.size < 2 || lastAt == Long.MIN_VALUE || atMs < lastAt || atMs - lastAt > maxAgeMs) {
            return Control("stop", false, 0.0, Double.POSITIVE_INFINITY)
        }
        var bestDist = Double.POSITIVE_INFINITY
        var bestLateral = 0.0
        var bestIndex = 0
        var bestT = 0.0
        for (i in 0 until path.size - 1) {
            val a = path[i]; val b = path[i + 1]
            val dx = b.x - a.x; val dy = b.y - a.y
            val len2 = dx * dx + dy * dy
            if (len2 <= 1e-8) continue
            val t = (((x - a.x) * dx + (y - a.y) * dy) / len2).coerceIn(0.0, 1.0)
            val px = a.x + t * dx; val py = a.y + t * dy
            val dist = hypot(x - px, y - py)
            if (dist < bestDist) {
                bestDist = dist; bestIndex = i; bestT = t
                // Positive means the user is on the left side of the path.
                bestLateral = dx * (y - py) - dy * (x - px)
                bestLateral /= max(hypot(dx, dy), 1e-6)
            }
        }
        if (!bestDist.isFinite() || bestDist > 1.2) return Control("stop", false, 0.0, bestDist)
        val look = 1.2 + min(2.0, hypot(vx, vy) * 0.8)
        var tx = path[bestIndex].x + (path[bestIndex + 1].x - path[bestIndex].x) * bestT
        var ty = path[bestIndex].y + (path[bestIndex + 1].y - path[bestIndex].y) * bestT
        var remaining = look
        var i = bestIndex
        var t = bestT
        while (i < path.size - 1 && remaining > 0.0) {
            val a = if (i == bestIndex) Point(tx, ty) else path[i]
            val b = path[i + 1]; val len = hypot(b.x - a.x, b.y - a.y)
            if (len > 1e-6) {
                val take = min(remaining, len); val f = take / len
                tx = a.x + (b.x - a.x) * f; ty = a.y + (b.y - a.y) * f; remaining -= take
            }
            i++
        }
        val speed = hypot(vx, vy)
        if (speed < 0.35) return Control("stop", false, 0.15, bestLateral)
        val targetAngle = atan2(ty - y, tx - x)
        val velocityAngle = atan2(vy, vx)
        val delta = ((targetAngle - velocityAngle + Math.PI * 3) % (Math.PI * 2)) - Math.PI
        val action = when {
            bestLateral > 0.18 || delta > Math.toRadians(22.0) -> "left"
            bestLateral < -0.18 || delta < -Math.toRadians(22.0) -> "right"
            else -> "straight"
        }
        val confidence = (1.0 - bestDist / 1.2).coerceIn(0.0, 1.0) *
            (1.0 - (atMs - lastAt).toDouble() / maxAgeMs).coerceIn(0.0, 1.0)
        return Control(action, true, confidence, bestLateral)
    }
}
