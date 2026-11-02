package com.blindnav.app

import kotlin.math.abs
import kotlin.math.sqrt

/** Image-detail heuristic only: neither a blur classifier nor evidence of a safe path. */
object FrameQuality {
    data class Summary(val brightness: Double, val contrast: Double, val detail: Double,
                       val limited: Boolean, val score: Double)

    /** ARGB pixels from a small, consistently scaled image; no Android dependency. */
    fun measure(pixels: IntArray, width: Int, height: Int): Summary {
        require(width > 0 && height > 0 && width.toLong() * height == pixels.size.toLong())
        val luma = DoubleArray(pixels.size) { i ->
            val c = pixels[i]
            ((c shr 16 and 255) * 77 + (c shr 8 and 255) * 150 + (c and 255) * 29) / 256.0
        }
        val mean = luma.average()
        val contrast = sqrt(luma.sumOf { (it - mean) * (it - mean) } / luma.size)
        var edge = 0.0
        var pairs = 0
        for (y in 0 until height) for (x in 0 until width) {
            val i = y * width + x
            if (x > 0) { edge += abs(luma[i] - luma[i - 1]); pairs++ }
            if (y > 0) { edge += abs(luma[i] - luma[i - width]); pairs++ }
        }
        val detail = if (pairs == 0) 0.0 else edge / pairs
        val limited = width < 3 || height < 3 || mean < 22 || mean > 245 || contrast < 8 || detail < 3
        val exposure = minOf(mean / 40, (255 - mean) / 40, 1.0).coerceAtLeast(0.0)
        return Summary(mean, contrast, detail, limited, detail * exposure)
    }
}

/** Debounce limitations, confirm recovery, and rate-limit warnings using capture time. */
class FrameQualityMonitor {
    data class Update(val limited: Boolean, val warn: Boolean)
    private var generation: Long? = null
    private var lastAt: Long? = null
    private var badSince: Long? = null
    private var goodSince: Long? = null
    private var badCount = 0
    private var goodCount = 0
    private var limited = false
    private var warnedAt: Long? = null

    @Synchronized fun reset() {
        generation = null; lastAt = null; badSince = null; goodSince = null
        badCount = 0; goodCount = 0; limited = false; warnedAt = null
    }

    @Synchronized fun observe(summary: FrameQuality.Summary, atNanos: Long, session: Long): Update {
        if (generation != session || lastAt?.let { atNanos <= it } == true) reset()
        // A capture gap breaks continuity, but cannot certify recovery from a known limitation.
        if (lastAt?.let { atNanos - it > 1_000_000_000L } == true) {
            badCount = 0; goodCount = 0; badSince = null; goodSince = null
        }
        generation = session; lastAt = atNanos
        if (summary.limited) {
            goodCount = 0; goodSince = null
            if (badSince == null) badSince = atNanos
            badCount = (badCount + 1).coerceAtMost(3)
            if (badCount >= 3 && atNanos - badSince!! >= 600_000_000L) limited = true
        } else {
            badCount = 0; badSince = null
            if (goodSince == null) goodSince = atNanos
            goodCount = (goodCount + 1).coerceAtMost(2)
            if (goodCount >= 2 && atNanos - goodSince!! >= 300_000_000L) limited = false
        }
        val warn = limited && (warnedAt == null || atNanos - warnedAt!! >= 30_000_000_000L)
        if (warn) warnedAt = atNanos
        return Update(limited, warn)
    }
}

/** At most three immutable frame references. The latest frame remains the fallback. */
class RecentFrameSelector<T> {
    private data class Entry<T>(val frame: T, val at: Long, val generation: Long,
                                val heading: Float?, val quality: FrameQuality.Summary)
    private val frames = ArrayDeque<Entry<T>>()
    @Synchronized fun reset() { frames.clear() }
    private fun near(a: Float?, b: Float?): Boolean = a != null && b != null &&
        a.isFinite() && b.isFinite() && abs(((a - b + 540f) % 360f) - 180f) <= 12f

    @Synchronized fun add(frame: T, atNanos: Long, generation: Long, heading: Float?, quality: FrameQuality.Summary) {
        val previous = frames.lastOrNull()
        if (previous != null && (previous.generation != generation || atNanos <= previous.at ||
                !near(previous.heading, heading))) frames.clear()
        frames.addLast(Entry(frame, atNanos, generation, heading, quality))
        while (frames.size > 3 || (frames.size > 1 && atNanos - frames.first().at > 450_000_000L)) frames.removeFirst()
    }

    @Synchronized fun select(nowNanos: Long, generation: Long, currentHeading: Float?): T? {
        val latest = frames.lastOrNull() ?: return null
        if (latest.generation != generation || nowNanos < latest.at) return null
        if (!near(latest.heading, currentHeading)) return latest.frame
        var best = latest
        for (candidate in frames) {
            if (candidate.generation == generation && nowNanos - candidate.at in 0L..450_000_000L &&
                near(candidate.heading, latest.heading) && near(candidate.heading, currentHeading) &&
                (candidate.quality.score > best.quality.score ||
                 (candidate.quality.score == best.quality.score && candidate.at > best.at))) best = candidate
        }
        return best.frame
    }
}
