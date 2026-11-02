package com.blindnav.app.last10m

/**
 * VLM 的结构化事实（任务书第八节）。
 *
 * 边界写死在这里，不靠提示词自觉：
 *  - VLM 只能回答"看不看得到、像不像同一店面、还缺什么视角"这类**事实**；
 *  - 它**不能**宣布到达、不能给移动距离、不能决定安全；
 *  - 任何越界字段（arrival/move/meters/safe…）被识别为 violation，值一律丢弃，
 *    只保留日志，最终状态永远由 [Last10mStateMachine] 与 [ArrivalAuthority] 决定。
 *
 * 本类刻意不 import org.json：核心逻辑要在纯 JVM 单元测试里跑真实代码，
 * JSON 解析放在 Android 侧的 Last10mSession 里做。
 */
data class VlmFacts(
    val targetVisible: Boolean?,
    val targetIdentity: String?,
    val candidateEntranceVisible: Boolean?,
    val entranceRelation: RelationOutcome,
    val neighboringEntrancePresent: Boolean?,
    val groundVisible: Boolean?,
    val needView: String?,
    val sourceFrameId: Long,
    val timestampMs: Long,
    val violations: List<String> = emptyList()
) {
    fun fresh(nowMs: Long, maxAgeMs: Long): Boolean = ageMs(nowMs, timestampMs) <= maxAgeMs

    companion object {
        /** 越界字段黑名单。命中即丢弃该键，并记入 violations。 */
        private val FORBIDDEN_KEYS = setOf(
            "arrival", "arrival_confirmed", "arrived", "confirmed",
            "move", "move_meters", "meters", "distance_meters", "walk",
            "step_back", "turn_left", "turn_right", "safe", "is_safe", "authority"
        )

        fun relationOf(raw: String?): RelationOutcome = when (raw?.trim()?.lowercase()) {
            "likely_same_storefront", "same_storefront", "same" -> RelationOutcome.LIKELY_SAME
            "conflict", "different_storefront", "neighbor", "not_same" -> RelationOutcome.CONFLICT
            else -> RelationOutcome.UNKNOWN
        }

        /**
         * 从已解析成 Map 的 VLM 回答构造事实。
         * 越界键不会被读进任何字段，只出现在 violations 里。
         */
        fun fromFields(
            fields: Map<String, Any?>,
            sourceFrameId: Long,
            timestampMs: Long
        ): VlmFacts {
            val violations = fields.keys.filter { it.lowercase() in FORBIDDEN_KEYS }
            return VlmFacts(
                targetVisible = bool(fields["target_visible"]),
                targetIdentity = (fields["target_identity"] as? String)?.takeIf { it.isNotBlank() },
                candidateEntranceVisible = bool(fields["candidate_entrance_visible"]),
                entranceRelation = relationOf(fields["entrance_relation"] as? String),
                neighboringEntrancePresent = bool(fields["neighboring_entrance_present"]),
                groundVisible = bool(fields["ground_visible"]),
                needView = (fields["need_view"] as? String)?.takeIf { it.isNotBlank() },
                sourceFrameId = sourceFrameId,
                timestampMs = timestampMs,
                violations = violations
            )
        }

        private fun bool(value: Any?): Boolean? = when (value) {
            null -> null
            is Boolean -> value
            is String -> value.lowercase().let { if (it == "true") true else if (it == "false") false else null }
            else -> null
        }
    }
}
