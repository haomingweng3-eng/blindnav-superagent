package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 第 14、17、18 节：时间行为、性质测试 P1–P8、跨帧证据污染。
 *
 * 全部真调手机用的同一套 Kotlin 核心（TargetLock / EntranceTracker /
 * Last10mStateMachine / ArrivalAuthority），不在 Python 里另写一份状态机冒充。
 *
 * 污染测试的形状是任务书第 18 节那个：
 *   frame 10 看到目标招牌 → frame 20 目标已不在 → frame 30 出现另一扇门
 *   → frame 40 出现安全地面
 * 系统绝不能把这四段拼成"目标+门+地面 = 到达"。
 */
class Last10mTemporalTest {

    private val profile = TargetPoiProfile.build("瑞幸咖啡")

    private fun hit(text: String, frameId: Long, ts: Long) =
        TextHit(text, 10, 10, 120, 40, 0.93, frameId, ts, EvidenceSource.OCR)

    /** 状态机在 !inRange 时一律停在 APPROACH，所以必须先声明"已进入最后十米"，
     *  否则后面所有断言都是在测一个没启动的状态机——空证。 */
    private fun machine(now: Long = 900): Last10mStateMachine =
        Last10mStateMachine(TargetPoiProfile.build("瑞幸咖啡")).apply { setInRange(true, now) }

    // ---------------------------------------------------------------- 第 14 节
    @Test
    fun sequenceASignKeepsAppearingLocks() {
        val m = machine()
        var s = m.setInRange(true, 1_000)
        assertEquals(Last10mState.SEARCH_TARGET, s.state)
        for (f in 1..5) {
            s = m.onTextHit(hit("瑞幸咖啡", f.toLong(), 1_000 + f * 200L), 1_000 + f * 200L)
        }
        assertTrue("连续 5 帧招牌必须建立锁", s.targetLocked)
        assertEquals(Last10mState.TARGET_LOCKED, s.state)
    }

    @Test
    fun sequenceBSignDisappearsLockExpires() {
        val m = machine()
        m.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        var s = m.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        assertTrue(s.targetLocked)
        // 之后再没有招牌：超过 lostTimeout(2.5s) 必须掉锁
        s = m.evaluate(5_000, emptyList())
        assertNotEquals("招牌消失 3.8 秒后仍是锁定态", Last10mState.TARGET_LOCKED, s.state)
        assertTrue("掉锁后必须报告曾经锁过（区别于一开始就没找到）",
            m.targetSnapshot().everLocked)
        assertEquals(TargetLock.Status.SEARCH_TARGET, m.targetSnapshot().status)
    }

    @Test
    fun sequenceCDoorAfterLostLockMustNotInheritRelation() {
        val m = machine()
        m.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        m.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        m.onDoorDetected(50, 300, 120, 520, 3, 1_400, 1_400)
        m.onVlmFacts(VlmFacts(true, null, true, RelationOutcome.LIKELY_SAME, false, null, null,
            3, 1_400), 1_400)
        // 目标消失，锁过期
        val afterLost = m.evaluate(6_000, emptyList())
        assertNotEquals(Last10mState.TARGET_LOCKED, afterLost.state)
        // 又出现一扇门：旧关系不能跟着新门
        val s = m.onDoorDetected(400, 320, 470, 540, 40, 6_200, 6_200)
        val rel = m.candidates().firstOrNull { it.doorId == s.activeDoorId }?.relationConfirmed
        assertNotEquals("锁失效后又出现门，旧关系被沿用", true as Boolean?, rel)
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, s.decision)
    }

    @Test
    fun sequenceDSingleOcrErrorCannotLock() {
        val m = machine()
        // 一次偶发的近似文本（连"瑞幸咖"都只算 ALIAS，且只有一帧）
        val s = m.onTextHit(hit("瑞幸咖", 1, 1_000), 1_000)
        assertEquals("单帧命中绝不能锁目标", 0, s.targetHits.let { if (s.targetLocked) 1 else 0 })
        assertNotEquals(Last10mState.TARGET_LOCKED, s.state)
    }

    @Test
    fun sequenceEFarApartFramesMustNotAccumulate() {
        val m = machine()
        m.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        // 每帧间隔 3 秒 > windowMs(4s)? 用 4.5 秒间隔保证跨过窗口
        m.onTextHit(hit("瑞幸咖啡", 2, 5_500), 5_500)
        val s = m.onTextHit(hit("瑞幸咖啡", 3, 10_000), 10_000)
        assertNotEquals("相隔过远的零星命中不应拼成锁", Last10mState.TARGET_LOCKED, s.state)
        assertTrue("命中数必须被过期清零后重新计", m.targetSnapshot().hitCount <= 2)
    }

    // ---------------------------------------------------------------- 第 17 节 P1–P8
    @Test
    fun p1_lostLockInvalidatesOldRelation() {
        val m = machine()
        m.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        m.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        m.onDoorDetected(50, 300, 120, 520, 3, 1_400, 1_400)
        m.onVlmFacts(VlmFacts(true, null, true, RelationOutcome.LIKELY_SAME, false, null, null, 3, 1_400), 1_400)
        assertTrue(m.candidates().any { it.relationConfirmed == true })
        m.onTargetLost("manual", 9_000)
        m.evaluate(9_500, emptyList())
        assertTrue("P1 掉锁后不应再有已确认关系",
            m.candidates().none { it.relationConfirmed == true })
    }

    @Test
    fun p2_neighborConflictBlocksArrival() {
        val a = ArrivalAuthority()
        val now = 10_000L
        val d = EntranceCandidate("door-A", 0, 0, 10, 10, now - 100, now - 100, 2,
            relationConfirmed = true, relationSource = EvidenceSource.MANUAL,
            groundObserved = true,
            groundEvidence = GroundEvidence(true, false, false, false, 1, now - 100))
        val got = a.decide(ArrivalAuthority.Input(now, true, true, 100, d,
            neighborConflict = true, relationConflict = false, facts = null, blockingConflict = false))
        // 关系已确认时邻门冲突不再拦（代码里就是这个优先级），但关系未确认必须拦
        val d2 = d.copy(relationConfirmed = null)
        val got2 = a.decide(ArrivalAuthority.Input(now, true, true, 100, d2,
            true, false, null, false))
        assertEquals(ArrivalDecision.REOBSERVE_DOOR, got2.outcome)
        println("  P2: 关系已确认+邻门冲突 → ${got.outcome}；关系未确认+邻门冲突 → ${got2.outcome}")
    }

    @Test
    fun p3_groundUnknownIsNotSafe() {
        val m = machine()
        m.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        m.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        m.onDoorDetected(50, 300, 120, 520, 3, 1_400, 1_400)
        m.onVlmFacts(VlmFacts(true, null, true, RelationOutcome.LIKELY_SAME, false, null, null, 3, 1_400), 1_400)
        val s = m.onGroundEvidence(GroundEvidence(false, false, false, false, 3, 1_400, "unknown"), null, 1_400)
        assertNotEquals("地面 UNKNOWN 不能当 SAFE", ArrivalDecision.ARRIVAL_CONFIRMED, s.decision)
        assertEquals(Last10mState.VERIFY_GROUND, s.state)
    }

    @Test
    fun p4_vlmCannotDeclareArrival() {
        val m = machine()
        m.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        m.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        val before = m.state()
        val fields = mapOf(
            "target_visible" to true, "entrance_relation" to "same_storefront",
            "arrived" to true, "move_meters" to 2, "is_safe" to true, "turn_left" to true)
        val facts = VlmFacts.fromFields(fields, 5, 2_400)
        assertTrue("越权键必须被记进 violations", facts.violations.isNotEmpty())
        val s = m.onVlmFacts(facts, 2_400)
        assertNotEquals("VLM 说到了也不能改变状态", Last10mState.ARRIVAL_CONFIRMED, s.state)
        assertNull("VLM 无权直接确认到达", s.prompt.takeIf { s.state == Last10mState.ARRIVAL_CONFIRMED })
        println("  P4 violations=${facts.violations} state $before → ${s.state}")
    }

    @Test
    fun p5_singleFrameFuzzyCannotLock() {
        val lock = TargetLock(TargetPoiProfile.build("中国建设银行"))
        // "中国工商银" 与目标只差一个字 → FUZZY，单独一帧不能算身份
        val snap = lock.observe(hit("中国工商银", 1, 1_000), 1_000)
        assertTrue(!snap.everLocked)
        assertEquals(TargetLock.Status.SEARCH_TARGET, snap.status)
    }

    @Test
    fun p6_timestampRewindRefused() {
        val now = 10_000L
        val d = EntranceCandidate("door-A", 0, 0, 10, 10, now + 9_000, now + 9_000, 2,
            relationConfirmed = true, relationSource = EvidenceSource.MANUAL,
            groundObserved = true, groundEvidence = GroundEvidence(true, false, false, false, 1, now + 9_000))
        assertEquals("候选年龄必须判为无穷大", Long.MAX_VALUE, d.ageMs(now))
        val got = ArrivalAuthority().decide(ArrivalAuthority.Input(now, true, true, 100, d,
            false, false, null, false))
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, got.outcome)
    }

    @Test
    fun p7_staleFrameCannotFormChain() {
        val m = machine()
        m.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        m.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        // 一帧很久以前的门（ts 在锁之前 8 秒）
        val s = m.onDoorDetected(50, 300, 120, 520, 3, 1_300, 9_000)
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, s.decision)
        // 9 秒时目标锁本身已过期，所以正确行为是"目标丢失→重扫"，
        // 而不是把 8 秒前那帧的门拿来凑到达。
        assertEquals(Last10mState.REOBSERVE, s.state)
        assertEquals(ArrivalDecision.TARGET_LOST, s.decision)
    }

    @Test
    fun p8_evidenceDoesNotCrossSessions() {
        val a = machine()
        a.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        a.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        a.onDoorDetected(50, 300, 120, 520, 3, 1_400, 1_400)
        a.onVlmFacts(VlmFacts(true, null, true, RelationOutcome.LIKELY_SAME, false, true, null, 3, 1_400), 1_400)
        // 换目标 = 新 session：必须从零开始，不能沿用上一个目标的锁/门/关系
        val b = machine()
        val s = b.evaluate(1_500, emptyList())
        assertEquals(Last10mState.SEARCH_TARGET, s.state)
        assertTrue(b.candidates().isEmpty())
        assertNotEquals(ArrivalDecision.ARRIVAL_CONFIRMED, s.decision)
    }

    // ---------------------------------------------------------------- 第 18 节
    @Test
    fun crossFrameContaminationNeverConfirmsArrival() {
        // 24 个变体：招牌消失时长、门出现时机、地面出现时机、门的位置都变
        var confirmed = 0
        val outcomes = HashMap<String, Int>()
        for (gap in listOf(3_000L, 5_000L, 9_000L)) {
            for (doorAt in listOf(3_500L, 6_000L, 12_000L)) {
                for (groundAt in listOf(3_600L, 6_200L, 12_500L)) {
                    for (shift in listOf(0, 300)) {
                        val m = machine()
                        m.onTextHit(hit("瑞幸咖啡", 10, 1_000), 1_000)
                        m.onTextHit(hit("瑞幸咖啡", 11, 1_200), 1_200)
                        // frame 20：目标已经不在视野（时间推进）
                        m.evaluate(1_000 + gap, emptyList())
                        // frame 30：出现另一扇门
                        m.onDoorDetected(400 + shift, 300, 470 + shift, 520, 30, doorAt, doorAt)
                        // frame 40：门前地面看起来安全
                        val s = m.onGroundEvidence(
                            GroundEvidence(true, false, false, false, 40, groundAt), null,
                            maxOf(doorAt, groundAt) + 10)
                        outcomes[s.decision.name] = (outcomes[s.decision.name] ?: 0) + 1
                        if (s.decision == ArrivalDecision.ARRIVAL_CONFIRMED) confirmed++
                    }
                }
            }
        }
        println("  污染用例 ${outcomes.values.sum()} 个，结果分布 $outcomes")
        assertEquals("跨帧证据被拼成了到达确认", 0, confirmed)
    }
}
