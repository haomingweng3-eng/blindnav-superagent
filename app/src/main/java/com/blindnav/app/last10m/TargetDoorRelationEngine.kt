package com.blindnav.app.last10m

import kotlin.math.max
import kotlin.math.min

/**
 * Target → Door 关系判定（任务书第 8 节）。
 *
 * 这是整条链以前最空的一段：招牌看到了、门也看到了，
 * 但"这扇是不是它家的门"从来没有人算过，于是只能靠 VLM 一句话或者人点一下。
 *
 * 三档输出，**UNKNOWN 是正常输出而不是失败**：
 *  - SAME_TARGET  有达到门槛的正向依据；
 *  - NEIGHBOR     有明确的反向依据（招牌属于另一家店，或者这块招牌明显压在别的门上）；
 *  - UNKNOWN      其他一切情况，包括"低置信度"。
 *
 * 两条不能妥协的原则：
 *  1. **低置信度必须 UNKNOWN，不许硬猜**。把隔壁门说成目标入口，是所有错误里代价最高的一种。
 *  2. **单来源不足以判 SAME_TARGET**。VLM 一句话、或者一条 OCR 挨着一扇门，都只算"线索"；
 *     要成为 SAME_TARGET，必须至少两条独立依据 agree
 *     （典型组合：VLM 说 same + 目标招牌在门上方；或 VLM 说 same + 门牌/地标支持）。
 *     两路来源都出自同一个 VLM 时不算两条独立依据——这是最容易自欺的地方。
 */
class TargetDoorRelationEngine(
    /** VLM 结论单独出现时的最低门槛。低于它一律 UNKNOWN。 */
    private val minVlmConfidence: Double = 0.60,
    /** 判定 SAME_TARGET 需要的最少独立依据数。 */
    private val requiredSupport: Int = 2,
    /** 招牌水平方向上要压在门上多少比例才算"这块招牌属于这扇门"。 */
    private val horizontalOverlap: Double = 0.30,
    /** 招牌与门的竖直间距，按门高度的倍数算；超过就不算同一店面。 */
    private val maxGapInDoorHeights: Double = 1.0
) {

    enum class Verdict { SAME_TARGET, NEIGHBOR, UNKNOWN }

    data class Evidence(
        val kind: String,
        val independent: Boolean,
        val detail: String
    )

    data class Decision(
        val doorId: String,
        val verdict: Verdict,
        val confidence: Double,
        val evidence: List<Evidence>,
        /** 为什么不是更硬的结论。面板要能回答"你怎么知道不是隔壁"。 */
        val whyUnknown: String?
    )

    /**
     * @param doors 当前候选门（像素框，来自 EntranceTracker 或视觉源）
     * @param texts 本帧/近帧的 OCR 命中（像素框）
     * @param visionRelations 视觉源给的关系结论（按门 id 或框对应）
     * @param profile 目标画像
     * @param groundSupport 门牌/地标支持（能力 2/3），没有就只靠招牌几何
     */
    fun decide(
        profile: TargetPoiProfile,
        doors: List<EntranceCandidate>,
        texts: List<TextHit>,
        visionRelations: List<VisionRelation>,
        doorIdToVisionId: Map<String, String>,
        addressSupport: Boolean? = null,
        landmarkConflict: Boolean = false
    ): List<Decision> {
        if (doors.isEmpty()) return emptyList()
        // 先把"哪些文字是目标的身份证据"挑出来，再算它们各自压在哪个门上
        val identityHits = texts.mapNotNull { t ->
            val m = profile.match(t.text) ?: return@mapNotNull null
            if (!profile.acceptsAsIdentity(m)) return@mapNotNull null
            t to m
        }
        val nonTargetSigns = texts.mapNotNull { t ->
            // 明确读到"另一家店"的名字：只能来自真实店名匹配，不能来自我们自己的名字片段
            val m = profile.match(t.text)
            if (m != null && profile.acceptsAsIdentity(m)) null
            else if (t.text.length >= 3 && !identityHits.any { it.first === t }) t else null
        }
        // 每扇门各自判断"有没有目标招牌压在我头上"。
        // 不能用 argmax 归属：一条宽招牌同时盖住两扇门，恰恰说明两扇都可能是它家的，
        // 这时两扇都要走 UNKNOWN；把招牌判给重叠最多的那扇、另一扇当隔壁，是凭空造出结论。
        val ownerDoors = identityHits.flatMap { hit ->
            doors.filter { overlapScore(hit.first, it) >= horizontalOverlap }.map { it.doorId }
        }.toSet()

        val out = ArrayList<Decision>()
        for (door in doors) {
            val evidence = ArrayList<Evidence>()
            var vlmConf: Double? = null
            var vlmVerdict: Verdict? = null
            val visionId = doorIdToVisionId[door.doorId]
            visionRelations.firstOrNull { it.doorId == visionId || it.doorId == door.doorId }?.let { r ->
                vlmConf = r.confidence
                vlmVerdict = when (r.relation) {
                    RelationOutcome.LIKELY_SAME -> Verdict.SAME_TARGET
                    RelationOutcome.CONFLICT -> Verdict.NEIGHBOR
                    RelationOutcome.UNKNOWN -> Verdict.UNKNOWN
                }
            }
            val strongestOverlap = overlapScoreOfBestSign(door, identityHits.map { it.first })
            val ownsSign = door.doorId in ownerDoors

            if (ownsSign) {
                evidence.add(Evidence("TARGET_SIGN_ABOVE_DOOR", true,
                    "目标招牌与门水平重叠 %.2f，竖直间距在门高以内".format(strongestOverlap)))
            }
            if (vlmVerdict == Verdict.SAME_TARGET && vlmConf != null && vlmConf >= minVlmConfidence) {
                // VLM 是**一路**来源，但和 OCR 几何不是同一路：算第二条独立依据。
                evidence.add(Evidence("VLM_SAME_STOREFRONT", false,
                    "VLM 判定同一店面 conf=%.2f（provisional）".format(vlmConf)))
            } else if (vlmVerdict == Verdict.SAME_TARGET) {
                evidence.add(Evidence("VLM_LOW_CONFIDENCE", false,
                    "VLM 说 same 但 conf=${vlmConf?.let { "%.2f".format(it) } ?: "null"} 低于门槛 $minVlmConfidence"))
            }
            if (vlmVerdict == Verdict.NEIGHBOR) {
                evidence.add(Evidence("VLM_NEIGHBOR", false, "VLM 判定这扇是隔壁（provisional）"))
            }
            if (addressSupport == true) {
                evidence.add(Evidence("ADDRESS_MATCH", true, "门牌/楼号与目标一致"))
            } else if (addressSupport == false) {
                evidence.add(Evidence("ADDRESS_CONFLICT", true, "门牌/楼号与目标不一致"))
            }
            if (landmarkConflict) {
                evidence.add(Evidence("LANDMARK_CONFLICT", true, "看到与目标互斥的地标"))
            }
            val otherDoorOwnsIt = nonTargetSigns.any { t -> overlapScore(t, door) >= horizontalOverlap } && !ownsSign
            if (otherDoorOwnsIt) {
                evidence.add(Evidence("OTHER_SIGN_OVER_THIS_DOOR", true,
                    "这扇门上方是别的店的招牌"))
            }

            val independentCount = evidence.count { it.independent }
            // 按**种类**计数而不是按条数：同一句话被两个源各报一次、或者一帧里出现两块同样的招牌，
            // 都不该被当成"两条独立依据"——那是最容易自欺的地方。
            val positiveKinds = evidence.map { it.kind }.toSet()
                .count { it == "TARGET_SIGN_ABOVE_DOOR" || it == "VLM_SAME_STOREFRONT" || it == "ADDRESS_MATCH" }
            val conflict = evidence.any { it.kind == "VLM_NEIGHBOR" || it.kind == "ADDRESS_CONFLICT" ||
                it.kind == "LANDMARK_CONFLICT" || it.kind == "OTHER_SIGN_OVER_THIS_DOOR" }
            val positive = positiveKinds > 0

            val verdict = when {
                conflict && !positive -> Verdict.NEIGHBOR
                conflict && positive -> Verdict.UNKNOWN        // 正反都有：不猜
                positiveKinds >= requiredSupport -> Verdict.SAME_TARGET
                else -> Verdict.UNKNOWN
            }
            val confidence = when (verdict) {
                Verdict.SAME_TARGET -> min(0.95, 0.60 + 0.15 * independentCount +
                    (vlmConf ?: 0.0) * 0.1)
                Verdict.NEIGHBOR -> 0.75
                Verdict.UNKNOWN -> (vlmConf ?: 0.0)
            }
            val why = when {
                verdict != Verdict.UNKNOWN -> null
                !ownsSign && vlmVerdict != Verdict.SAME_TARGET -> "既没有压在门上的目标招牌，也没有 VLM 支持"
                !ownsSign && (vlmConf ?: 0.0) < minVlmConfidence ->
                    "只有 VLM 一句话且置信度不够（${"%.2f".format(vlmConf ?: 0.0)}）"
                ownsSign && vlmVerdict != Verdict.SAME_TARGET -> "招牌支持只有 1 条，不足以确认"
                conflict -> "正反依据同时存在，拒绝下结论"
                else -> "依据不足"
            }
            out.add(Decision(door.doorId, verdict, confidence, evidence, why))
        }
        // 多扇门都被支持时，一律降级 UNKNOWN——宁可让用户多看一眼，也不挑一扇"看起来像"的
        val same = out.count { it.verdict == Verdict.SAME_TARGET }
        return if (same >= 2) out.map {
            if (it.verdict == Verdict.SAME_TARGET)
                it.copy(verdict = Verdict.UNKNOWN, whyUnknown = "有 ${same} 扇门都被支持，分不清哪扇")
            else it
        } else out
    }

    /** 招牌与门框的水平重叠比例（以招牌宽度为分母），加上"招牌在门上方"的竖直约束。 */
    private fun overlapScore(sign: TextHit, door: EntranceCandidate): Double {
        if (door.width <= 0 || door.height <= 0 || sign.right <= sign.left) return 0.0
        val left = max(sign.left, door.left)
        val right = min(sign.right, door.right)
        val horizontal = if (right <= left) 0.0 else (right - left).toDouble() / max(1, sign.right - sign.left)
        // 招牌应该在门的上方或同一高度带；招牌在门下面很多，多半是隔壁的门牌或海报
        val gap = door.top - sign.bottom
        if (gap < -door.height * 0.5) return 0.0
        if (gap > door.height * maxGapInDoorHeights) return 0.0
        return horizontal
    }

    private fun overlapScoreOfBestSign(door: EntranceCandidate, signs: List<TextHit>): Double =
        signs.maxOfOrNull { overlapScore(it, door) } ?: 0.0

    private val EntranceCandidate.width get() = right - left
    private val EntranceCandidate.height get() = bottom - top
}
