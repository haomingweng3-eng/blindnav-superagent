package com.blindnav.app

/**
 * Conservative policy for an optional depth provider. It does not access ARCore
 * or infer distance from an RGB frame; absent, stale, unsupported, or uncertain
 * depth remains unknown and cannot clear the last-ten-metre safety gate.
 */
object DepthEvidence {
    data class Sample(
        val supported: Boolean,
        val distanceMeters: Double?,
        val confidence: Double?,
        val ageMs: Long,
        val groundContinuous: Boolean
    )
    data class Result(val level: String, val reason: String, val usable: Boolean)

    fun evaluate(sample: Sample?, maxAgeMs: Long = 500L): Result {
        if (sample == null || !sample.supported) return Result("unknown", "设备未提供深度", false)
        val distance = sample.distanceMeters
        val confidence = sample.confidence
        if (sample.ageMs !in 0..maxAgeMs || distance == null || !distance.isFinite() ||
            confidence == null || !confidence.isFinite() || confidence !in 0.0..1.0) {
            return Result("unknown", "深度数据缺失或已过期", false)
        }
        if (distance < 0.5) return Result("near_hazard", "镜头下方近距离空间可能有落差或障碍", true)
        if (distance <= 5.0 && confidence >= 0.6 && sample.groundContinuous) {
            return Result("relative_clear", "近距离地面深度连续", true)
        }
        return Result("unknown", "深度范围或地面连续性不足", false)
    }
}
