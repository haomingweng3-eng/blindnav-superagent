package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

/**
 * 第 16 节：ArrivalAuthority 模糊测试（≥10,000 组随机证据状态）。
 *
 * 唯一硬性不变量：**六条不齐，绝不能 ARRIVAL_CONFIRMED**。
 *   1 目标身份已确认  2 有入口候选  3 关系已确认  4 证据新鲜
 *   5 门前地面已观察  6 无邻门/阻挡冲突
 *
 * 这个测试的价值全在"随机覆盖到人工想不到的组合"，所以断言只写不变量，
 * 不写"某组输入应该出某个具体提示"——那种断言会把实现锁死在巧合上。
 */
class Last10mArrivalFuzzTest {

    private val maxEvidenceAge = 2000L
    private val maxRelationAge = 4000L

    private fun door(
        lastSeenMs: Long,
        relationConfirmed: Boolean?,
        groundObserved: Boolean,
        ground: GroundEvidence?,
        relationSource: EvidenceSource?
    ) = EntranceCandidate(
        doorId = "door-fuzz", left = 10, top = 20, right = 60, bottom = 90,
        firstSeenMs = lastSeenMs - 400, lastSeenMs = lastSeenMs, seenFrames = 3,
        relationConfirmed = relationConfirmed, relationSource = relationSource,
        groundObserved = groundObserved, groundEvidence = ground
    )

    /** 六条是否真的齐备——按 ArrivalAuthority 的文档语义独立写一遍，不复用它自己的判断。 */
    private fun sixConditionsHold(i: ArrivalAuthority.Input, nowMs: Long): Boolean {
        val d = i.activeDoor ?: return false
        val g = d.groundEvidence
        val groundOk = g != null && g.visible && !g.obstruction &&
            ageMs(nowMs, g.timestampMs) <= maxEvidenceAge
        val vlmRelationFresh = d.relationSource !in setOf(
            EvidenceSource.VLM, EvidenceSource.REMOTE_VLM, EvidenceSource.REPLAY) ||
            (i.facts?.fresh(nowMs, maxRelationAge) ?: false)
        return i.targetLocked &&
            d.relationConfirmed == true &&
            d.groundObserved &&
            groundOk &&
            !i.relationConflict &&
            !(i.neighborConflict && d.relationConfirmed != true) &&
            !i.blockingConflict &&
            i.targetAgeMs <= maxEvidenceAge &&
            d.ageMs(nowMs) <= maxEvidenceAge &&
            vlmRelationFresh
    }

    @Test
    fun fuzzTenThousandRandomEvidenceStates() {
        val rnd = Random(20261102)
        val authority = ArrivalAuthority(maxEvidenceAgeMs = maxEvidenceAge, maxRelationAgeMs = maxRelationAge)
        var confirmed = 0
        var counterexamples = 0
        val seen = HashSet<String>()
        val firstBad = ArrayList<String>()
        repeat(10_000) { k ->
            val now = 100_000L
            // 纯随机很难同时凑齐六条（实测 10k 里 0~1 次），那样"反例=0"是空证。
            // 所以 1/3 的样本从"完全合法状态"出发再随机摘掉 0–2 条，
            // 其余仍是全随机，用来撞人工想不到的组合。
            val seeded = rnd.nextInt(3) == 0
            val drop = if (seeded) rnd.nextInt(3) else 0
            fun fresh() = rnd.nextLong(0, 1500)
            fun stale() = rnd.nextLong(0, 6000)
            val doorLast = now - (if (seeded && drop != 1) fresh() else stale())
            val groundAge = if (seeded && drop != 2) fresh() else stale()
            val groundVisible = if (seeded) drop != 3 else rnd.nextBoolean()
            val obstruction = if (seeded) drop == 4 else rnd.nextBoolean()
            val ground = if (seeded || rnd.nextBoolean()) GroundEvidence(
                visible = groundVisible, step = rnd.nextBoolean(), curb = rnd.nextBoolean(),
                obstruction = obstruction,
                sourceFrameId = k.toLong(), timestampMs = now - groundAge) else null
            val relation = if (seeded) (if (drop == 5) null else true)
                else when (rnd.nextInt(3)) { 0 -> true; 1 -> false; else -> null }
            val src = if (seeded) EvidenceSource.MANUAL
                else when (rnd.nextInt(3)) { 0 -> EvidenceSource.VLM; 1 -> EvidenceSource.OCR; else -> EvidenceSource.MANUAL }
            val d = door(doorLast, relation, ground?.visible == true, ground, src)
            val facts = if (rnd.nextBoolean()) VlmFacts(
                targetVisible = rnd.nextBoolean(), targetIdentity = null,
                candidateEntranceVisible = rnd.nextBoolean(),
                entranceRelation = RelationOutcome.values()[rnd.nextInt(3)],
                neighboringEntrancePresent = rnd.nextBoolean(),
                groundVisible = rnd.nextBoolean(), needView = null,
                sourceFrameId = k.toLong(), timestampMs = now - rnd.nextLong(0, 8000)) else null
            val input = ArrivalAuthority.Input(
                nowMs = now,
                targetLocked = if (seeded) drop != 6 else rnd.nextBoolean(),
                targetEverLocked = rnd.nextBoolean(),
                targetAgeMs = if (seeded && drop != 7) fresh() else stale(),
                activeDoor = if (seeded || rnd.nextBoolean()) d else null,
                neighborConflict = if (seeded) drop == 8 else rnd.nextBoolean(),
                relationConflict = if (seeded) drop == 9 else rnd.nextBoolean(),
                facts = facts,
                blockingConflict = if (seeded) drop == 10 else rnd.nextBoolean()
            )
            val sig = "${input.targetLocked}/${input.targetEverLocked}/${input.activeDoor?.relationConfirmed}/" +
                "${input.activeDoor?.groundObserved}/${input.neighborConflict}/${input.relationConflict}/" +
                "${input.blockingConflict}/${input.targetAgeMs > maxEvidenceAge}"
            seen.add(sig)
            val decision = authority.decide(input)
            if (decision.outcome == ArrivalDecision.ARRIVAL_CONFIRMED) {
                confirmed++
                if (!sixConditionsHold(input, now)) {
                    counterexamples++
                    if (firstBad.size < 5) firstBad.add(sig + " → " + decision.reason)
                }
            }
        }
        println("FUZZ 10k: 到达确认 $confirmed 次，覆盖不同状态组合 ${seen.size} 种，反例 $counterexamples")
        firstBad.forEach { println("  反例 $it") }
        assertTrue("fuzz 必须真的覆盖到确认到达，否则这个测试是空跑", confirmed > 0)
        assertEquals("发现六条不齐却确认到达的反例：$counterexamples", 0, counterexamples)
    }

    @Test
    fun removingEachOfTheSixConditionsBlocksArrival() {
        val now = 100_000L
        val authority = ArrivalAuthority(maxEvidenceAgeMs = maxEvidenceAge, maxRelationAgeMs = maxRelationAge)
        val goodGround = GroundEvidence(visible = true, step = false, curb = false, obstruction = false,
            sourceFrameId = 9L, timestampMs = now - 100)
        fun goodDoor(
            lastSeen: Long = now - 100,
            relation: Boolean? = true,
            ground: GroundEvidence? = goodGround,
            groundObserved: Boolean = true,
            src: EvidenceSource? = EvidenceSource.MANUAL
        ) = door(lastSeen, relation, groundObserved, ground, src)

        fun decide(d: EntranceCandidate?, locked: Boolean = true, everLocked: Boolean = true,
                  targetAge: Long = 100, neighbor: Boolean = false, relationConflict: Boolean = false,
                  blocking: Boolean = false, facts: VlmFacts? = null) =
            authority.decide(ArrivalAuthority.Input(now, locked, everLocked, targetAge, d,
                neighbor, relationConflict, facts, blocking))

        // 基准：六条齐备必须确认
        assertEquals(ArrivalDecision.ARRIVAL_CONFIRMED, decide(goodDoor()).outcome)

        val cases = listOf(
            "1 目标身份未确认" to decide(null, locked = false, everLocked = true),
            "2 无入口候选" to decide(null),
            "3 关系未判定" to decide(goodDoor(relation = null)),
            "3 关系判成隔壁" to decide(goodDoor(relation = false)),
            "4 目标证据过期" to decide(goodDoor(), targetAge = 9000),
            "4 门证据过期" to decide(goodDoor(lastSeen = now - 9000)),
            "5 地面未观察" to decide(goodDoor(ground = null, groundObserved = false)),
            "5 地面 UNKNOWN" to decide(goodDoor(ground = goodGround.copy(visible = false))),
            "5 地面有阻挡" to decide(goodDoor(ground = goodGround.copy(obstruction = true))),
            "6 邻门冲突且关系未确认" to decide(goodDoor(relation = null), neighbor = true),
            "6 关系冲突" to decide(goodDoor(), relationConflict = true),
            "6 阻挡冲突" to decide(goodDoor(), blocking = true),
            "VLM 关系但事实过期" to decide(goodDoor(src = EvidenceSource.VLM),
                facts = VlmFacts(true, null, true, RelationOutcome.LIKELY_SAME, false, true, null,
                    1L, now - 9999)),
            "时间戳倒退" to decide(goodDoor(lastSeen = now + 5000))
        )
        for ((name, got) in cases) {
            println("  摘掉 $name → ${got.outcome}")
            assertNotEquals("摘掉「$name」之后仍然 ARRIVAL_CONFIRMED", ArrivalDecision.ARRIVAL_CONFIRMED, got.outcome)
        }
    }

    /**
     * 第 18 节里最阴的一种：地面证据是很久以前那一帧看到的。
     *
     * ArrivalAuthority 目前只检查"门证据新鲜"和"目标证据新鲜"，
     * 没有检查 `groundEvidence.timestampMs` 的新鲜度。
     * 于是"30 秒前低头看到过一次安全地面 + 现在刚认出门和关系"也能凑齐六条。
     * 这条测试就是把这个洞钉住：它必须不能确认到达。
     */
    @Test
    fun staleGroundEvidenceMustNotConfirmArrival() {
        val now = 100_000L
        val authority = ArrivalAuthority(maxEvidenceAgeMs = maxEvidenceAge, maxRelationAgeMs = maxRelationAge)
        val ancientGround = GroundEvidence(visible = true, step = false, curb = false, obstruction = false,
            sourceFrameId = 1L, timestampMs = now - 30_000)     // 30 秒前那一帧的地面
        val d = door(now - 100, true, true, ancientGround, EvidenceSource.MANUAL)
        val decision = authority.decide(ArrivalAuthority.Input(now, true, true, 100, d,
            false, false, null, false))
        println("  旧地面(30s 前) 现在判定 = ${decision.outcome} / ${decision.reason}")
        assertNotEquals("旧的地面证据被当成新鲜证据用了：$decision",
            ArrivalDecision.ARRIVAL_CONFIRMED, decision.outcome)
    }

    /** 地面明明看到过，但这一帧不在画面里——groundObserved 这个标志位不能单独放行。 */
    @Test
    fun groundFlagWithoutFreshEvidenceDoesNotConfirm() {
        val now = 100_000L
        val authority = ArrivalAuthority(maxEvidenceAgeMs = maxEvidenceAge, maxRelationAgeMs = maxRelationAge)
        val d = door(now - 100, true, true, null, EvidenceSource.MANUAL)   // flag=true 但 evidence=null
        val decision = authority.decide(ArrivalAuthority.Input(now, true, true, 100, d,
            false, false, null, false))
        assertEquals(ArrivalDecision.LOOK_DOWN_GROUND, decision.outcome)
    }
}
