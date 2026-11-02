package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

/**
 * 夜间全量模糊测试：100,000 组随机证据状态（任务书第 18/19 节）。
 *
 * 保留 10k 那份快跑，这里加 100k 深跑。两件事一起验，方向都写全：
 *
 *  A. **ARRIVAL_CONFIRMED ⇔ 十一条前置条件全部成立**。
 *     `guards()` 是照文档**独立重写的一份**条件表，不是复用 decide() 里的分支；
 *     所以两个方向都断言才能真正抓到"分支与文档不一致"。
 *     单向断言（只查 ARRIVAL ⇒ 条件齐）会漏掉"条件明明齐了却不宣布到达"——
 *     那是功能坏掉，不是安全问题，同样必须被抓出来。
 *  B. 跨会话与未来时间戳这类"看着像新鲜其实不新鲜"的组合，随机出来也必须拦住。
 *
 * 十一条：targetConfirmed / targetFresh / doorCandidate / doorFresh / relationConfirmed /
 * relationFresh / groundObserved / groundFresh / noConflict / sameSession / timestampValid。
 */
class Last10mNightlyFuzzTest {

    private val maxEvidenceAge = 2000L
    private val maxRelationAge = 4000L
    private val sessions = listOf("S-A", "S-B", "S-C", null)

    /** 关系结论的"临时来源"：模型/云端/回放给的判断，都要在窗口内重新确认才算数。 */
    private val PROVISIONAL_RELATION = setOf(
        EvidenceSource.VLM, EvidenceSource.REMOTE_VLM, EvidenceSource.REPLAY)

    /**
     * 独立写一遍全部前置条件（照文档，不复用 decide() 的分支，也不复用 guards()）。
     * 名字与 [ArrivalAuthority.guards] 一一对应，好让"哪一条走偏了"能直接打出来。
     */
    private fun independentGuards(i: ArrivalAuthority.Input): List<Pair<String, Boolean>> {
        val d = i.activeDoor
        val g = d?.groundEvidence
        val now = i.nowMs
        return listOf(
            "targetConfirmed" to i.targetLocked,
            "targetFresh" to (i.targetAgeMs <= maxEvidenceAge),
            "doorCandidate" to (d != null),
            "doorFresh" to (d != null && d.ageMs(now) <= maxEvidenceAge),
            "relationConfirmed" to (d?.relationConfirmed == true),
            // 文档语义（独立重写）：**临时来源**的关系结论只在窗口内算数。
            // 临时来源 = 任何 VLM 通道（本地标注的 VLM、云端 REMOTE_VLM）+ 回放 REPLAY；
            // 人工与本地模型给的是当帧标签/框，不查这一条新鲜度。
            // 并且必须说得出"什么时候判的"：拿不到时间戳的临时结论按不新鲜处理。
            "relationFresh" to (d != null && d.relationConfirmed == true &&
                (d.relationSource !in PROVISIONAL_RELATION ||
                    (i.facts != null && i.facts.fresh(now, maxRelationAge)) ||
                    (i.facts == null && d.relationEvidenceMs > 0L &&
                        ageMs(now, d.relationEvidenceMs) <= maxRelationAge))),
            "groundObserved" to (g != null && g.visible && !g.obstruction),
            "groundFresh" to (g != null && ageMs(now, g.timestampMs) <= maxEvidenceAge),
            "noConflict" to (!i.relationConflict && !i.blockingConflict &&
                !(i.neighborConflict && d?.relationConfirmed != true)),
            "sameSession" to (d != null && d.sessionId == i.sessionId &&
                (g == null || g.sessionId == i.sessionId)),
            "timestampValid" to (d != null && d.lastSeenMs <= now &&
                (g == null || g.timestampMs <= now) && i.targetAgeMs != Long.MAX_VALUE)
        )
    }

    /**
     * 造一组随机证据状态。三种配比，缺一不可：
     *  - 30% **完全合法**状态：这样"unsafe=0"不是空证——到达确认确实发生得了；
     *  - 30% **只破坏一条**前置条件的状态：这是最有信息量的对抗形状，
     *    每条防线都必须单独拦住到达；
     *  - 40% 全随机：用来撞人工想不到的组合（含未来时间戳、跨会话、来源混乱）。
     */
    private fun randomState(rnd: Random, k: Int): ArrivalAuthority.Input {
        val now = 100_000L
        val shape = rnd.nextInt(10)
        val fullyLegal = shape < 3
        val oneBroken = shape in 3..5
        val randomish = shape >= 6
        val broken = rnd.nextInt(12)

        fun br(which: Int) = oneBroken && broken == which
        fun coin(p: Int) = rnd.nextInt(p) == 0
        val fresh = { rnd.nextLong(0, 1_900) }
        val old = { rnd.nextLong(3_000, 7_000) }

        val session = if (coin(4)) null else sessions[rnd.nextInt(sessions.size)]
        val otherSession = sessions.filter { it != session }.let {
            if (it.isEmpty()) listOf<String?>(null) else it
        }
        // 会话身份：默认全程一致；只有被指定破坏、或纯随机时才漂走
        val doorSession: String? = if (br(10) || (randomish && coin(4))) otherSession[rnd.nextInt(otherSession.size)] else session
        val groundSession: String? = if (br(10)) doorSession else session
        val futureTs = br(11) || (randomish && coin(8))
        val doorLastSeen = now - (if (br(3) || (randomish && coin(2))) old() else fresh()) +
            if (br(11)) 4_000 else 0
        val groundVisible = !br(6) && (!randomish || rnd.nextBoolean())
        val ground: GroundEvidence? = when {
            br(6) -> GroundEvidence(false, false, false, false, k.toLong(), now - fresh(), "",
                GroundSource.DEBUG_MANUAL, groundSession)
            randomish && coin(5) -> null
            else -> GroundEvidence(
                visible = groundVisible, step = rnd.nextBoolean(), curb = rnd.nextBoolean(),
                obstruction = if (fullyLegal) false else (randomish && coin(3)),
                sourceFrameId = k.toLong(),
                timestampMs = now - (if (br(7)) old() else fresh()) + if (futureTs) 5_000 else 0,
                note = "", source = GroundSource.values()[rnd.nextInt(GroundSource.values().size)],
                sessionId = groundSession
            )
        }
        val relation: Boolean? = when {
            br(4) -> if (coin(2)) null else false
            randomish -> when (rnd.nextInt(3)) { 0 -> true; 1 -> false; else -> null }
            else -> true
        }
        val door = if (br(2) || (randomish && coin(6))) null else EntranceCandidate(
            doorId = "door-$k", left = 10, top = 20, right = 60, bottom = 90,
            firstSeenMs = doorLastSeen - 300, lastSeenMs = doorLastSeen, seenFrames = rnd.nextInt(1, 6),
            relationConfirmed = relation,
            relationSource = if (br(5)) EvidenceSource.VLM
                else EvidenceSource.values()[rnd.nextInt(EvidenceSource.values().size)],
            groundObserved = ground?.visible == true,
            groundEvidence = ground,
            sourceFrameId = k.toLong(),
            source = CandidateSource.values()[rnd.nextInt(CandidateSource.values().size)],
            sessionId = doorSession
        )
        val facts = when {
            br(5) -> VlmFacts(true, null, true, RelationOutcome.UNKNOWN, false, true, null,
                k.toLong(), now - old())
            door?.relationSource == EvidenceSource.VLM && door.relationConfirmed == true -> VlmFacts(
                true, null, true, RelationOutcome.LIKELY_SAME, false, true, null,
                k.toLong(), now - fresh())
            randomish && coin(2) -> VlmFacts(
                rnd.nextBoolean(), null, rnd.nextBoolean(),
                RelationOutcome.values()[rnd.nextInt(3)], rnd.nextBoolean(), rnd.nextBoolean(),
                null, k.toLong(), now - rnd.nextLong(0, 6_000))
            else -> null
        }
        return ArrivalAuthority.Input(
            nowMs = now,
            targetLocked = if (randomish) rnd.nextBoolean() else !br(0),
            targetEverLocked = true,
            targetAgeMs = if (br(1) || (randomish && coin(2))) old() else fresh(),
            activeDoor = door,
            neighborConflict = br(9) || (randomish && coin(2)),
            relationConflict = br(8) || (randomish && coin(4)),
            facts = facts,
            blockingConflict = (randomish && coin(4)) && !br(10),
            sessionId = session
        )
    }

    @Test
    fun fuzzHundredThousandStatesNeverBreakTheArrivalContract() {
        val rnd = Random(20261102L)
        val authority = ArrivalAuthority(maxEvidenceAgeMs = maxEvidenceAge, maxRelationAgeMs = maxRelationAge)
        var confirmed = 0
        var unsafe = 0
        var missedArrival = 0
        var combos = 0
        var sixMismatch = 0
        val seenCombos = HashSet<String>()
        val firstBad = ArrayList<String>()
        repeat(100_000) { k ->
            val i = randomState(rnd, k)
            val d = authority.decide(i).outcome
            val guards = authority.guards(i)
            val allHold = guards.all { it.second }
            if (d == ArrivalDecision.ARRIVAL_CONFIRMED) confirmed++
            if (allHold) combos++
            seenCombos.add(guards.joinToString("") { if (it.second) "1" else "0" })
            if (d == ArrivalDecision.ARRIVAL_CONFIRMED && !allHold) {
                unsafe++
                if (firstBad.size < 3) firstBad.add("ARRIVAL 但条件不齐：$guards\n  input=$i")
            }
            if (allHold && d != ArrivalDecision.ARRIVAL_CONFIRMED) {
                missedArrival++
                if (firstBad.size < 3) firstBad.add("条件齐却没宣布到达：${authority.decide(i)}\n  input=$i")
            }
            // 第三份实现（文档语义独立重写）也必须同意，防止 guards() 自己写歪
            val mine = independentGuards(i)
            if (mine.all { it.second } != allHold) {
                sixMismatch++
                val diff = guards.zip(mine).filter { it.first.second != it.second.second }
                    .joinToString("; ") { "${it.first.first}: guards=${it.first.second} mine=${it.second.second}" }
                if (firstBad.size < 3) firstBad.add("两处条件表走偏 → $diff\n  door=${i.activeDoor} ground=${i.activeDoor?.groundEvidence} session=${i.sessionId}")
            }
        }
        println("FUZZ 100k：到达确认=$confirmed 条件全成立=$combos " +
            "覆盖条件组合=${seenCombos.size}/1024 unsafe=$unsafe missed=$missedArrival " +
            "guards 与独立六条不一致=$sixMismatch")
        firstBad.forEach { println("  反例：$it") }
        assertEquals("不安全 ARRIVAL 必须为 0", 0, unsafe)
        assertEquals("条件齐备却不宣布到达也必须为 0（否则是功能坏了）", 0, missedArrival)
        assertEquals("guards() 与独立写的六条语义必须完全同构", 0, sixMismatch)
        assertTrue("100k 必须真的覆盖到多种条件组合（实测 ${seenCombos.size}）", seenCombos.size >= 64)
        assertTrue("必须出现过合法到达，否则整轮都是空证（实测 $confirmed 次）", confirmed > 0)
    }

    /** §19：随机切换会话，至少 100 组，任何一段会话的证据都不能给另一段背书。 */
    @Test
    fun crossSessionContaminationAcrossHundredRandomSwitches() {
        val rnd = Random(4242)
        val profiles = listOf("瑞幸咖啡", "星巴克咖啡", "全家便利店", "中国建设银行")
        var arrivalWithForeignEvidence = 0
        var arrivalsInOwnSession = 0
        var switches = 0
        val notes = ArrayList<String>()
        repeat(100) { round ->
            val shared = EntranceTracker()
            val first = profiles[round % profiles.size]
            val second = profiles[(round + 1) % profiles.size]
            val a = Last10mStateMachine(TargetPoiProfile.build(first), tracker = shared, sessionId = "R$round-A")
            a.setInRange(true, 1_000)
            a.onTextHit(TextHit(first, 0, 0, 60, 20, 0.9, 1, 1_000), 1_000)
            a.onTextHit(TextHit(first, 0, 0, 60, 20, 0.9, 2, 1_150), 1_150)
            a.onDebugDoor(DebugDoorPosition.values()[rnd.nextInt(3)], 1080, 1920, 3, 1_250, 1_250)
            a.onVlmFacts(VlmFacts.fromFields(mapOf("target_identity" to first,
                "entrance_relation" to "same_storefront"), 4, 1_300), 1_300)
            a.onGroundEvidence(GroundEvidence(true, false, false, false, 5, 1_350, "",
                GroundSource.DEBUG_MANUAL, "R$round-A"), null, 1_350)
            val ra = a.evaluate(1_350, emptyList())
            if (ra.decision == ArrivalDecision.ARRIVAL_CONFIRMED) arrivalsInOwnSession++

            // 换目标、换会话，但候选门与地面证据还是上一段攒下来的
            val b = Last10mStateMachine(TargetPoiProfile.build(second), tracker = shared, sessionId = "R$round-B")
            b.setInRange(true, 1_400)
            b.onTextHit(TextHit(second, 0, 0, 60, 20, 0.9, 11, 1_400), 1_400)
            b.onTextHit(TextHit(second, 0, 0, 60, 20, 0.9, 12, 1_500), 1_500)
            val rb = b.evaluate(1_550, listOf(second))
            switches++
            if (rb.decision == ArrivalDecision.ARRIVAL_CONFIRMED) {
                arrivalWithForeignEvidence++
                if (notes.size < 3) notes.add("反例 round=$round ${first}→${second} candidates=${shared.candidates().map { it.sessionId }}")
            }
            // 而且旧会话的目标名不该变成新会话的身份证据
            assertTrue("新会话的身份依据只能是新目标名（实测 ${b.targetSnapshot().matchedAlias}）",
                b.targetSnapshot().matchedAlias != first || first == second)
        }
        println("CROSS SESSION：切换 $switches 次，本会话合法到达 $arrivalsInOwnSession 次，" +
            "跨会话误到达 $arrivalWithForeignEvidence 次")
        notes.forEach { println("  $it") }
        assertEquals("跨会话证据绝不能拼出到达", 0, arrivalWithForeignEvidence)
        assertTrue("必须真的出现过合法到达（否则这轮是空证：$arrivalsInOwnSession）",
            arrivalsInOwnSession >= 90)
    }

    /** 一帧内的两路结果不能冒充"多帧确认"（同一来源同一帧重复喂的极端情况）。 */
    @Test
    fun sameFrameRepeatCannotFakeMultiFrameLock() {
        val rnd = Random(7)
        var locked = 0
        repeat(2_000) {
            val m = Last10mStateMachine(TargetPoiProfile.build("瑞幸咖啡"), sessionId = "S")
            m.setInRange(true, 1_000)
            val frame = 1L + rnd.nextInt(3)
            repeat(rnd.nextInt(1, 8)) {
                m.onTextHit(TextHit("瑞幸咖啡", 0, 0, 60, 20, 0.9, frame, 1_000L + it * 10), 1_050L + it * 10)
            }
            if (m.evaluate(1_100, emptyList()).targetLocked) locked++
        }
        println("SAME-FRAME REPEAT：单帧重复喂入 2000 次里锁定 $locked 次")
        assertEquals("同一帧内重复命中不得凑成多帧确认", 0, locked)
    }
}
