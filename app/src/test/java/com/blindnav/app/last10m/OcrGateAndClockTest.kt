package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OCR 帧闸门与看门狗（任务书第 8/9 节 / PF-10、PF-11、PF-12）。
 *
 * 上一版的三个具体风险：
 *  - `Tasks.await(task)` 没有超时：ML Kit 内部卡住 ⇒ `busy` 永久为真 ⇒ 之后每帧都被丢掉，
 *    唯一症状是面板上 `dropped` 一直涨，App 不崩也不报错（PF-10）；
 *  - 被 300ms 节流挡掉的帧没有计数，面板显示的丢帧率是低的（PF-11）；
 *  - 节流用墙上时钟，回拨会把 OCR 冻在那段时间里（PF-12）。
 *
 * 这一层是纯 Kotlin + 注入时钟，所以这三件事都能在电脑上写成确定性测试。
 */
class OcrGateAndClockTest {

    private class FakeClock(start: Long = 0L) : () -> Long {
        var now = start
            private set
        override fun invoke(): Long = now
        fun advance(ms: Long) { now += ms }
        fun set(ms: Long) { now = ms }
    }

    private fun gate(clock: FakeClock, minIntervalMs: Long = 300, timeoutMs: Long = 3_000,
                     sink: MutableList<String> = ArrayList()) =
        OcrGate(minIntervalMs, timeoutMs, clock = clock, events = { sink.add(it) })

    // ---------------------------------------------------------------- 节流与丢弃
    @Test
    fun throttledFramesAreCountedSeparatelyFromDropped() {
        val clock = FakeClock(1_000)
        val gate = gate(clock)
        assertNotNull("第一帧必须进", gate.tryBegin(1))
        gate.finish(1L, 1, 10_000, true)
        clock.advance(100)                              // 还没到 300ms
        assertNull("节流期内不进", gate.tryBegin(2))
        assertEquals("被节流挡掉的帧要单独计数（PF-11）", 1, gate.throttled)
        assertEquals("节流派丢的不算 dropped", 0, gate.dropped)
        clock.advance(250)
        assertNotNull("过了 300ms 就可以进", gate.tryBegin(3))
        println("  节流统计：${gate.stats()}")
    }

    @Test
    fun busyGateDropsInsteadOfQueueing() {
        val clock = FakeClock(0)
        val events = ArrayList<String>()
        val g = gate(clock, sink = events)
        assertNotNull(g.tryBegin(1))                    // 第一帧在跑，不 finish
        clock.advance(400)
        assertNull("上一帧还在跑 ⇒ 这一帧直接丢，绝不排队", g.tryBegin(2))
        assertEquals(1, g.dropped)
        assertTrue(events.any { it.startsWith("OCR_BUSY_DROP") })
        assertEquals("OCR_BUSY_DROP", events.last().split(" ").first())
    }

    // ---------------------------------------------------------------- 看门狗
    @Test
    fun stuckOcrIsReleasedByWatchdogAndNextFrameRuns() {
        val clock = FakeClock(0)
        val events = ArrayList<String>()
        val g = gate(clock, timeoutMs = 3_000, sink = events)
        val gen1 = g.tryBegin(1)
        assertNotNull(gen1)
        // OCR-1 卡住，永不 finish
        clock.set(2_999)
        assertNull("没到超时前不放开", g.tryBegin(2))
        clock.set(3_001)
        val gen2 = g.tryBegin(2)
        assertNotNull("看门狗必须放开闸门，让更新的一帧进去（OCR-2 一定执行）", gen2)
        assertEquals(1, g.timeouts)
        assertTrue("必须留下 OCR_TIMEOUT 事件：$events",
            events.any { it.startsWith("OCR_TIMEOUT") && it.contains("frame=1") })
        println("  看门狗：$gen1 → $gen2，事件=${events.filter { it.startsWith("OCR_") }}")
    }

    @Test
    fun lateResultIsIgnoredAndCannotContaminateState() {
        val clock = FakeClock(0)
        val events = ArrayList<String>()
        val g = gate(clock, timeoutMs = 3_000, sink = events)
        val gen1 = g.tryBegin(1)!!
        clock.set(3_001)
        val gen2 = g.tryBegin(2)!!
        assertFalse("迟到结果不能交付给状态机", g.finish(gen1, 1, 9_000_000, true))
        assertEquals(1, g.lateResultsIgnored)
        assertTrue("更新那一帧的结果照常交付", g.finish(gen2, 2, 120_000, true))
        assertTrue(events.any { it.startsWith("OCR_LATE_RESULT_IGNORED") })
        println("  迟到作废：${events.first { it.startsWith("OCR_LATE") }}")
    }

    @Test
    fun timeoutFollowedByNormalRunIsProcessed() {
        // 任务书第 8 节的验收形状：OCR-1 超时，然后 OCR-2 正常返回 ⇒ 第二次一定执行。
        val clock = FakeClock(0)
        val delivered = ArrayList<Long>()
        val g = OcrGate(300, 3_000, clock = clock) { }
        val gen1 = g.tryBegin(10)
        g.finish(gen1!!, 10, 5_000, ok = false)          // 超时/失败也要释放闸门
        clock.advance(400)
        val gen2 = g.tryBegin(11)
        assertNotNull("上一次失败后必须还能进下一帧", gen2)
        if (g.finish(gen2!!, 11, 90_000, ok = true)) delivered.add(11L)
        assertEquals(listOf(11L), delivered)
        assertEquals("busy 必须已经放开", false, g.busy())
    }

    @Test
    fun busyFlagIsNeverLeftStuck() {
        val clock = FakeClock(0)
        val events = ArrayList<String>()
        val g = gate(clock, timeoutMs = 1_000, sink = events)
        var accepted = 0
        repeat(50) { i ->
            clock.advance(1_200)                          // 每次都超过超时，但每次都故意不 finish
            if (g.tryBegin(i.toLong()) != null) accepted++
        }
        // "永久 busy"的表现就是：之后一帧也进不去。这里要求几乎每帧都能进。
        assertTrue("反复卡死也必须一直有帧能进去（实测 $accepted/50）", accepted >= 45)
        assertEquals("闸门里挂着的必须是最新那一帧", 49L, g.inFlightFrame())
        assertTrue("每次卡死都要留下超时事件：timeouts=${g.timeouts}", g.timeouts >= 40)
        println("  50 次全卡死：accepted=$accepted timeouts=${g.timeouts} dropped=${g.dropped} " +
            "inFlight=${g.inFlightFrame()}")
    }

    // ---------------------------------------------------------------- 单调时钟
    @Test
    fun clockRewindDoesNotFreezeOcr() {
        val clock = FakeClock(100_000)
        val events = ArrayList<String>()
        val g = gate(clock, sink = events)
        g.finish(g.tryBegin(1)!!, 1, 1_000, true)
        clock.set(10_000)                                 // 模拟时钟倒退
        assertNotNull("倒退不能把节流永久冻住", g.tryBegin(2))
        assertTrue(events.any { it.startsWith("CLOCK_REWIND") })
        println("  时钟倒退：${events.first { it.startsWith("CLOCK_REWIND") }}")
    }

    @Test
    fun statsLineCarriesEveryCounterThePanelShows() {
        val clock = FakeClock(0)
        val g = gate(clock, timeoutMs = 1_000)
        g.finish(g.tryBegin(1)!!, 1, 10, true)
        clock.advance(50); g.tryBegin(2)                  // 节流
        clock.advance(500); g.tryBegin(3)                 // 接受但不 finish
        clock.advance(2_000); g.tryBegin(4)               // 看门狗后接受
        val s = g.stats()
        println("  stats=$s")
        for (key in listOf("runs", "dropped", "throttled", "timeouts", "late", "busy", "inFlight")) {
            assertTrue("面板要显示的每一项都得在 stats 里：$s", s.contains("$key="))
        }
    }

    // ---------------------------------------------------------------- 会话隔离（§19 P8 扩展）
    @Test
    fun evidenceFromAnotherSessionNeverConfirmsArrival() {
        val profile = TargetPoiProfile.build("瑞幸咖啡")
        val shared = EntranceTracker()
        val a = Last10mStateMachine(profile, tracker = shared, sessionId = "SESSION-A")
        a.setInRange(true, 1_000)
        a.onTextHit(TextHit("瑞幸咖啡", 0, 0, 50, 20, 0.9, 1, 1_000), 1_000)
        a.onTextHit(TextHit("瑞幸咖啡", 0, 0, 50, 20, 0.9, 2, 1_200), 1_200)
        a.onDebugDoor(DebugDoorPosition.CENTER, 1000, 1000, 3, 1_300, 1_300)
        a.onVlmFacts(VlmFacts.fromFields(mapOf(
            "target_identity" to "瑞幸咖啡", "entrance_relation" to "same_storefront"), 4, 1_400), 1_400)
        a.onGroundEvidence(GroundEvidence(true, false, false, false, 5, 1_500, "",
            GroundSource.DEBUG_MANUAL, "SESSION-A"), null, 1_500)
        assertEquals("前提：同一会话内可以到达",
            ArrivalDecision.ARRIVAL_CONFIRMED, a.evaluate(1_500, emptyList()).decision)
        assertEquals("候选必须带会话身份", "SESSION-A", a.candidates().single().sessionId)
        assertEquals("地面证据也必须带会话身份",
            "SESSION-A", a.candidates().single().groundEvidence?.sessionId)

        // 换一个目标/会话，但候选与地面证据还是上一段会话攒下来的。
        // B 自己重新锁定了目标，时间上也全都"新鲜"——只剩会话身份这一条拦着。
        val b = Last10mStateMachine(profile, tracker = shared, sessionId = "SESSION-B")
        b.setInRange(true, 1_600)
        b.onTextHit(TextHit("瑞幸咖啡", 0, 0, 50, 20, 0.9, 11, 1_600), 1_600)
        b.onTextHit(TextHit("瑞幸咖啡", 0, 0, 50, 20, 0.9, 12, 1_700), 1_700)
        val rb = b.evaluate(1_700, listOf("瑞幸咖啡"))
        assertTrue("旧候选的门龄必须仍在新鲜期内（否则这个测试没测到会话这条）",
            shared.candidates().single().ageMs(1_700) < 2_000)
        assertFalse("跨会话证据不得参与本会话到达判定",
            rb.decision == ArrivalDecision.ARRIVAL_CONFIRMED)
        assertEquals(ArrivalDecision.REOBSERVE_TARGET, rb.decision)
        assertTrue("面板必须说清原因：${rb.reasonCodes}",
            rb.reasonCodes.contains(ReasonCode.SESSION_MISMATCH))
        println("  §19 A 会话可到达；B 复用同一 tracker → ${rb.decision} reasons=${rb.reasonCodes}")
    }

    /** 直接对裁决层做跨会话断言：这是最省事的验证路径。 */
    @Test
    fun authorityRejectsCrossSessionDoorAndGround() {
        val doorA = EntranceCandidate("door-1", 0, 400, 100, 900, 1_300, 1_400, 2,
            relationConfirmed = true, relationSource = EvidenceSource.VLM,
            groundObserved = true,
            groundEvidence = GroundEvidence(true, false, false, false, 5, 1_500, "",
                GroundSource.DEBUG_MANUAL, "SESSION-A"),
            sourceFrameId = 3, source = CandidateSource.DEBUG_MANUAL_PROPOSAL,
            sessionId = "SESSION-A")
        val authority = ArrivalAuthority()
        fun input(session: String?) = ArrivalAuthority.Input(
            nowMs = 1_500, targetLocked = true, targetEverLocked = true, targetAgeMs = 300,
            activeDoor = doorA, neighborConflict = false, relationConflict = false,
            facts = VlmFacts.fromFields(mapOf("entrance_relation" to "same_storefront"), 4, 1_400),
            blockingConflict = false, sessionId = session)
        assertEquals("同会话应当允许到达", ArrivalDecision.ARRIVAL_CONFIRMED,
            authority.decide(input("SESSION-A")).outcome)
        val other = authority.decide(input("SESSION-B"))
        assertFalse("跨会话绝不能到达", other.outcome == ArrivalDecision.ARRIVAL_CONFIRMED)
        assertEquals(ArrivalDecision.REOBSERVE_TARGET, other.outcome)
        assertTrue("理由必须点名会话：${other.reason}", other.reason.contains("另一个会话"))
        val guards = authority.guards(input("SESSION-B")).filter { !it.second }.map { it.first }
        assertTrue("独立条件表也必须标出 sameSession 不成立：$guards", guards.contains("sameSession"))
        println("  跨会话裁决：${other.outcome} / ${other.reason}")
    }
}
