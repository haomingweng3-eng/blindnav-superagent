package com.blindnav.app.last10m

/**
 * 地标辅助证据（能力 2）。
 *
 * 只靠目标店名一件事是很脆的：OCR 少一个字、招牌被树挡一半、或者店招是纯 logo，
 * 目标就认不出来。但用户走到楼前其实还有别的证据——商场名、楼号、银行名、
 * 门口那块大标识。把它们分开记账，就能在"店名没读全"时说一句
 * "你大概率已经在正确的建筑附近"，而不是硬说找到了目标。
 *
 * 三条硬规矩：
 *  1. 地标**不能**确认到达。它只提高"到没到这栋建筑"的把握，
 *     到达仍然要过 [ArrivalAuthority] 的六条。
 *  2. supporting / conflicting 列表由调用方（POI 记录/用户）给，
 *     **不从店名自动派生**。上一轮已经证明自动派生别名会塌缩成两三个字到处乱认。
 *  3. 同一帧里出现互斥地标（例如目标是星巴克，门上方读着瑞幸咖啡）算 CONFLICTING，
 *     要压住确认，而不是当成"反正都是咖啡店"。
 */
enum class LandmarkRole { PRIMARY_TARGET, SUPPORTING_LANDMARK, CONFLICTING_LANDMARK, UNKNOWN }

data class LandmarkHit(
    val text: String,
    val role: LandmarkRole,
    val score: Double,
    val sourceFrameId: Long,
    val timestampMs: Long,
    /** 与门的相对位置，用于"门上方读着什么"这类判断；null = 还不知道 */
    val doorId: String? = null
)

class LandmarkIndex(
    private val profile: TargetPoiProfile,
    private val supporting: List<String> = emptyList(),
    private val conflicting: List<String> = emptyList()
) {
    /** supporting/conflicting 用归一化后**完全相等或明确包含**判定，不走模糊档。 */
    private val sup = supporting.map { TextNormalizer.normalize(it) }.filter { it.length >= 2 }
    private val con = conflicting.map { TextNormalizer.normalize(it) }.filter { it.length >= 2 }

    fun classify(hit: TextHit): LandmarkHit {
        val normalized = TextNormalizer.normalize(hit.text)
        val targetMatch = profile.match(hit.text)
        val role = when {
            // 与目标主名**完全相等**才算主证据。
            // 否则"星巴克万象城店"这种 POI 名会把商场名"万象城"包含进去，
            // 只看懂一块商场招牌就被当成认出了店——那是我们最不想要的宽松。
            targetMatch != null && profile.acceptsAsIdentity(targetMatch) &&
                (normalized == TextNormalizer.normalize(profile.poiName) ||
                    sup.none { normalized == it }) -> LandmarkRole.PRIMARY_TARGET
            con.any { normalized.contains(it) } -> LandmarkRole.CONFLICTING_LANDMARK
            sup.any { normalized == it || normalized.contains(it) } -> LandmarkRole.SUPPORTING_LANDMARK
            targetMatch != null -> LandmarkRole.PRIMARY_TARGET   // 弱目标命中（含 FUZZY），够不够由 TargetLock 判
            else -> LandmarkRole.UNKNOWN
        }
        return LandmarkHit(
            text = hit.text, role = role,
            score = targetMatch?.score ?: 0.0,
            sourceFrameId = hit.sourceFrameId, timestampMs = hit.timestampMs, doorId = null
        )
    }

    fun hasSupporting(): Boolean = supporting.isNotEmpty()
    fun hasConflicting(): Boolean = conflicting.isNotEmpty()
}

/**
 * 一帧内地标汇总。规则很朴素：
 *  - 有 PRIMARY → 目标证据在
 *  - 只有 SUPPORTING → 可以提示"应该到这栋建筑附近了"
 *  - 只有 CONFLICTING 且没有 PRIMARY → 冲突
 *  - 都没有 → UNKNOWN（不猜）
 */
class LandmarkLedger(private val maxAgeMs: Long = 4000L) {
    private val hits = ArrayList<LandmarkHit>()

    fun add(hit: LandmarkHit) { hits.add(hit) }

    fun prune(nowMs: Long) { hits.removeAll { ageMs(nowMs, it.timestampMs) > maxAgeMs } }

    fun counts(nowMs: Long): Map<LandmarkRole, Int> {
        prune(nowMs)
        return hits.groupingBy { it.role }.eachCount()
    }

    /** 冲突判定：看到互斥地标，且这段时间里没有任何目标主证据。 */
    fun conflict(nowMs: Long): Boolean {
        val c = counts(nowMs)
        return (c[LandmarkRole.CONFLICTING_LANDMARK] ?: 0) > 0 &&
            (c[LandmarkRole.PRIMARY_TARGET] ?: 0) == 0
    }

    /** 辅助确认：有支持性地标、没有主证据、也没有冲突。 */
    fun nearBuildingOnly(nowMs: Long): Boolean {
        val c = counts(nowMs)
        return (c[LandmarkRole.SUPPORTING_LANDMARK] ?: 0) > 0 &&
            (c[LandmarkRole.PRIMARY_TARGET] ?: 0) == 0 &&
            (c[LandmarkRole.CONFLICTING_LANDMARK] ?: 0) == 0
    }

    fun reset() = hits.clear()
}
