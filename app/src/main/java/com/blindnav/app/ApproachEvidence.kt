package com.blindnav.app

/** Conservative apparent-approach evidence. It never estimates meters. */
class ApproachEvidence(private val maxGapMs: Long = 2000L) {
    data class Result(val approaching: Boolean, val urgent: Boolean)
    private data class Last(val width: Float, val at: Long)
    private val last = HashMap<String, Last>()

    fun reset() = last.clear()

    fun observe(key: String, width: Float, band: String, atMs: Long): Result {
        val prev = last[key]
        last[key] = Last(width, atMs)
        if (prev == null || atMs < prev.at || atMs - prev.at > maxGapMs || width <= 0f || !width.isFinite()) {
            return Result(false, band == "近")
        }
        val growth = width / prev.width
        val approaching = growth >= 1.4f
        return Result(approaching, band == "近" || approaching && growth >= 1.6f)
    }
}
