package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Target → Door 关系判定（任务书第 8 节）。
 *
 * 这一段以前是整条链最大的洞：招牌看到了、门也看到了，"这扇是不是它家的"从来没人算。
 * 今晚必须算，而且要算得保守 —— 把隔壁门说成目标入口，是所有错误里代价最高的一种。
 *
 * 断言的核心不是"猜对"，而是**该说不知道的时候必须说不知道**。
 */
class TargetDoorRelationEngineTest {

    private val profile = TargetPoiProfile.build("瑞幸咖啡", listOf("LUCKIN COFFEE"))

    private fun door(id: String, left: Int, top: Int, right: Int, bottom: Int) =
        EntranceCandidate(id, left, top, right, bottom, 1_000L, 1_500L, 3, sessionId = "S")

    /** 招牌在门的正上方：横向重叠够、竖直间距在门高以内。 */
    private fun sign(text: String, left: Int, top: Int, right: Int, bottom: Int) =
        TextHit(text, left, top, right, bottom, 0.9, 7L, 1_400L, EvidenceSource.LOCAL_OCR)

    private fun decide(
        doors: List<EntranceCandidate>,
        texts: List<TextHit>,
        relations: List<VisionRelation>,
        mapping: Map<String, String> = doors.associate { it.doorId to it.doorId.removePrefix("door-").let { n -> "door_$n" } },
        engine: TargetDoorRelationEngine = TargetDoorRelationEngine(),
        address: Boolean? = null,
        landmarkConflict: Boolean = false
    ) = engine.decide(profile, doors, texts, relations, mapping, address, landmarkConflict)
        .associateBy { it.doorId }

    @Test
    fun vlmAloneIsNeverEnough() {
        // 只有一句话、没有本地招牌几何 ⇒ UNKNOWN。两个"独立依据"必须是不同来源的不同证据。
        val d = decide(listOf(door("door-1", 100, 700, 300, 1_600)), emptyList(),
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.9)))
        assertEquals(TargetDoorRelationEngine.Verdict.UNKNOWN, d.getValue("door-1").verdict)
        println("RELATION 只有 VLM：${d["door-1"]?.whyUnknown}")
    }

    @Test
    fun lowConfidenceVlmIsTreatedAsUnknown() {
        val texts = listOf(sign("瑞幸咖啡", 120, 300, 300, 400))
        val d = decide(listOf(door("door-1", 100, 700, 300, 1_600)), texts,
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.42)))
        val one = d.getValue("door-1")
        assertEquals("VLM 置信度不够就不能算第二条依据",
            TargetDoorRelationEngine.Verdict.UNKNOWN, one.verdict)
        assertTrue("要留下低置信度这条记录：${one.evidence}",
            one.evidence.any { it.kind == "VLM_LOW_CONFIDENCE" })
        assertTrue(one.evidence.any { it.kind == "TARGET_SIGN_ABOVE_DOOR" })
    }

    @Test
    fun signGeometryPlusVlmConfirmsSameTarget() {
        val d = decide(listOf(door("door-1", 100, 700, 300, 1_600)),
            listOf(sign("瑞幸咖啡", 120, 300, 300, 400)),
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.75)))
        assertEquals(TargetDoorRelationEngine.Verdict.SAME_TARGET, d.getValue("door-1").verdict)
        assertTrue(d.getValue("door-1").confidence >= 0.6)
        println("RELATION 招牌+VLM → SAME_TARGET conf=${"%.2f".format(d.getValue("door-1").confidence)}")
    }

    @Test
    fun neighbourDoorIsRejectedWhileTargetDoorIsAccepted() {
        // 这就是明天要演的那一幕：两扇并排门，一扇目标、一扇隔壁
        val doors = listOf(door("door-1", 0, 700, 330, 1_600), door("door-2", 660, 700, 990, 1_600))
        val d = decide(doors, listOf(sign("瑞幸咖啡", 10, 300, 320, 400)),
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.6),
                VisionRelation("door_2", RelationOutcome.CONFLICT, 0.4)))
        assertEquals("目标那扇要确认", TargetDoorRelationEngine.Verdict.SAME_TARGET, d.getValue("door-1").verdict)
        assertEquals("隔壁那扇要判成 NEIGHBOR", TargetDoorRelationEngine.Verdict.NEIGHBOR, d.getValue("door-2").verdict)
        println("RELATION 邻门场景 → ${d.mapValues { it.value.verdict }}")
    }

    @Test
    fun twoSupportedDoorsFallBackToUnknown() {
        // 两扇都被支持时不能"挑一个看起来更象的"：那是最容易铸错的一种自信
        val doors = listOf(door("door-1", 0, 700, 400, 1_600), door("door-2", 600, 700, 990, 1_600))
        val texts = listOf(sign("瑞幸咖啡", 0, 300, 990, 420))   // 一整条宽招牌，两扇都被压住
        val d = decide(doors, texts,
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.8),
                VisionRelation("door_2", RelationOutcome.LIKELY_SAME, 0.8)))
        assertTrue("两扇都被支持时必须都不确认：${d.mapValues { it.value.verdict }}",
            d.values.all { it.verdict == TargetDoorRelationEngine.Verdict.UNKNOWN })
        assertTrue("要说明为什么：${d.values.map { it.whyUnknown }}",
            d.values.all { (it.whyUnknown ?: "").contains("都被支持") })
    }

    @Test
    fun otherStoreSignOverTheDoorMeansNeighbour() {
        // 门上方是别家的招牌：目标招牌离得很远，这扇只能是隔壁
        val doors = listOf(door("door-1", 100, 700, 300, 1_600))
        val texts = listOf(sign("中国建设银行", 100, 300, 300, 400))
        val d = decide(doors, texts, emptyList())
        assertEquals(TargetDoorRelationEngine.Verdict.NEIGHBOR, d.getValue("door-1").verdict)
        println("RELATION 别家招牌压顶 → ${d.getValue("door-1").evidence.map { it.kind }}")
    }

    @Test
    fun signBelowDoorIsNotEvidence() {
        // 招牌在门下方很多（多半是海报或门牌），不能算"这块招牌属于这扇门"
        val d = decide(listOf(door("door-1", 100, 100, 300, 400)),
            listOf(sign("瑞幸咖啡", 120, 900, 300, 980)),
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.9)))
        assertEquals(TargetDoorRelationEngine.Verdict.UNKNOWN, d.getValue("door-1").verdict)
    }

    @Test
    fun addressSupportCountsAsSecondIndependentEvidence() {
        val d = decide(listOf(door("door-1", 100, 700, 300, 1_600)),
            listOf(sign("瑞幸咖啡", 120, 300, 300, 400)), emptyList(), address = true)
        assertEquals("门牌一致 + 招牌压顶 = 够格", TargetDoorRelationEngine.Verdict.SAME_TARGET,
            d.getValue("door-1").verdict)
        val conflicting = decide(listOf(door("door-1", 100, 700, 300, 1_600)),
            listOf(sign("瑞幸咖啡", 120, 300, 300, 400)),
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.9)), address = false)
        assertEquals("门牌不一致时正反都有 ⇒ 不猜",
            TargetDoorRelationEngine.Verdict.UNKNOWN, conflicting.getValue("door-1").verdict)
    }

    @Test
    fun landmarkConflictBlocksConfirmation() {
        val d = decide(listOf(door("door-1", 100, 700, 300, 1_600)),
            listOf(sign("瑞幸咖啡", 120, 300, 300, 400)),
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.9)),
            landmarkConflict = true)
        assertEquals(TargetDoorRelationEngine.Verdict.UNKNOWN, d.getValue("door-1").verdict)
    }

    @Test
    fun sameSourceTwiceIsStillOneSource() {
        // 两条 VLM 关系（同一帧里模型自己说两次）不能凑成"两条独立依据"
        val engine = TargetDoorRelationEngine(requiredSupport = 2)
        val d = engine.decide(profile,
            listOf(door("door-1", 100, 700, 300, 1_600)),
            listOf(sign("瑞幸咖啡", 120, 300, 300, 400)),
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.9),
                VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.95)),
            mapOf("door-1" to "door_1"))
            .associateBy { it.doorId }
        assertEquals("同一类依据只算一种：${d.getValue("door-1").evidence.map { e -> e.kind }}",
            TargetDoorRelationEngine.Verdict.SAME_TARGET, d.getValue("door-1").verdict)
        // ↑ 这里是 SAME_TARGET，因为招牌几何是本地 OCR（另一路来源），VLM 只算一路。
        //   真正的"同源两票不算"由下面这条断言守住：
        val onlyVlm = engine.decide(profile, listOf(door("door-1", 100, 700, 300, 1_600)),
            emptyList(),
            listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.9),
                VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.99)),
            mapOf("door-1" to "door_1"))
        assertTrue("只有 VLM 反复说，一律 UNKNOWN",
            onlyVlm.all { it.verdict == TargetDoorRelationEngine.Verdict.UNKNOWN })
    }
}
