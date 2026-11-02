package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 感知编排器：什么时候问、问谁、问不到怎么办（任务书第 5/6/19 节）。
 *
 * 要守住的四件事：
 *  - 不该问的时候一次网络都不发（省电，也避免旧答案赶上新画面）；
 *  - 一源失败必须自动降到下一源，并且**每一跳都留下原因**；
 *  - 不可信的结果一个字段都不能用；
 *  - 来源必须一路带到状态机与面板，不能把回放说成实时识别。
 */
class PerceptionOrchestratorTest {

    private val profile = TargetPoiProfile.build("瑞幸咖啡", listOf("LUCKIN COFFEE"))

    private class FakeReasoner(
        override val source: EvidenceSource,
        private val responder: (Int) -> Last10mVisionResult?
    ) : VisionReasoner {
        var calls = 0
        override fun analyze(jpeg: ByteArray?, frameId: Long, timestampMs: Long,
                             target: TargetPoiProfile, context: VisionContext): Last10mVisionResult? {
            calls++
            return responder(calls - 1)
        }
    }

    private fun result(
        source: EvidenceSource,
        doors: List<Pair<String, IntArray>> = listOf("door_1" to intArrayOf(0, 400, 300, 900)),
        relations: List<VisionRelation> = listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.8)),
        ground: VisionGround = VisionGround(true, false, false, false),
        texts: List<TextHit> = listOf(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 1L, 1_000L, source)),
        scene: String? = null,
        parseOk: Boolean = true,
        doorType: DoorType = DoorType.UNKNOWN,
        button: ButtonLocation = ButtonLocation.UNKNOWN
    ) = Last10mVisionResult(1L, 1_000L, source, texts,
        doors.map { VisionDoor(it.first, it.second, 0.8) },
        targetVisible = true, targetIdentity = "瑞幸咖啡", relations = relations,
        ground = ground, doorType = doorType, buttonLocation = button,
        sceneSummary = scene, parseOk = parseOk,
        violations = if (parseOk) emptyList() else listOf("TEST_FORCED"))

    private fun harness(
        reasoners: List<VisionReasoner>,
        minIntervalMs: Long = 2_500L,
        clockStart: () -> Long
    ): Triple<Last10mStateMachine, PerceptionOrchestrator, MutableList<String>> {
        val machine = Last10mStateMachine(profile, sessionId = "S")
        val events = ArrayList<String>()
        val orch = PerceptionOrchestrator(machine, profile, TargetDoorRelationEngine(),
            reasoners, clockStart, minIntervalMs, events = { events.add(it) },
            frameSize = { 1080 to 1920 })
        return Triple(machine, orch, events)
    }

    @Test
    fun nothingIsAskedBeforeTheTargetIsLocked() {
        var now = 1_000L
        val clock = { now }
        val cloud = FakeReasoner(EvidenceSource.REMOTE_VLM) { result(EvidenceSource.REMOTE_VLM) }
        val (machine, orch, _) = harness(listOf(cloud), clockStart = clock)
        machine.setInRange(true, now)
        repeat(10) { now += 500L; assertNull("还没认出目标就不该问云端", orch.tick(ByteArray(4), it.toLong(), now, now)) }
        assertEquals(0, cloud.calls)
        // 现在锁上目标（两帧不同帧号）
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 1L, now, EvidenceSource.LOCAL_OCR), now)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 2L, now, EvidenceSource.LOCAL_OCR), now + 100)
        now += 800     // 还在身份证据窗口内，才轮到检验"该不该问云端"
        assertNotNull("目标锁定且没有门 ⇒ 该问了", orch.tick(ByteArray(4), 9L, now, now))
        assertEquals(1, cloud.calls)
        println("TRIGGER 锁目标前 0 次、之后 1 次")
    }

    @Test
    fun minIntervalGatesRepeatRequests() {
        var now = 1_000L
        val cloud = FakeReasoner(EvidenceSource.REMOTE_VLM) { result(EvidenceSource.REMOTE_VLM) }
        val (machine, orch, _) = harness(listOf(cloud), clockStart = { now })
        machine.setInRange(true, now)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 1L, now, EvidenceSource.LOCAL_OCR), now)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 2L, now + 50, EvidenceSource.LOCAL_OCR), now + 50)
        val probe2 = machine.evaluate(now + 200, emptyList())
        println("PROBE2 now=${now + 200} locked=${probe2.targetLocked} snap=${machine.targetSnapshot()} " +
            "shouldAsk=${orch.shouldAskNow(now + 200)}")
        assertNotNull(orch.tick(ByteArray(2), 3L, now + 200, now + 200))
        assertNull("最小间隔内不再问第二次", orch.tick(ByteArray(2), 4L, now + 500, now + 500))
        assertNull(orch.tick(ByteArray(2), 5L, now + 800, now + 800))
        // 人还在看着那块招牌：OCR 继续给命中，身份证据不会因为"过了节流窗"而过期
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 6L, now + 2_400, EvidenceSource.LOCAL_OCR), now + 2_400)
        assertNotNull("过了间隔才允许再问", orch.tick(ByteArray(2), 7L, now + 2_700, now + 2_700))
        assertEquals(2, cloud.calls)
    }

    @Test
    fun fallsThroughTheChainAndKeepsEveryReason() {
        var now = 0L
        val cloud = FakeReasoner(EvidenceSource.REMOTE_VLM) { null }        // 没网/没 key
        val replay = FakeReasoner(EvidenceSource.REPLAY) { _ ->
            result(EvidenceSource.REPLAY, parseOk = false)                  // 有回答但不可信
        }
        val manual = FakeReasoner(EvidenceSource.DEBUG_MANUAL) { _ ->
            result(EvidenceSource.DEBUG_MANUAL, doorType = DoorType.PUSH_PULL)
        }
        val (machine, orch, events) = harness(listOf(cloud, replay, manual), clockStart = { now })
        machine.setInRange(true, 1_000)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 1L, 1_000, EvidenceSource.LOCAL_OCR), 1_000)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 2L, 1_100, EvidenceSource.LOCAL_OCR), 1_100)
        now = 3_000
        val out = orch.tick(ByteArray(2), 3L, now, now)
        assertNotNull("必须降级到人工源，不能整场哑掉", out)
        assertTrue(out!!.applied)
        assertEquals(EvidenceSource.DEBUG_MANUAL, out.source)
        assertEquals(listOf("NO_RESULT", "UNPARSEABLE=[TEST_FORCED]", "ANSWERED"),
            out.trail.map { it.outcome })
        assertTrue(events.any { it.startsWith("VISION_ALL_FAILED") } ||
            events.any { it.contains("VISION_SOURCE_SKIP") })
        println("CHAIN 降级链=${out.trail.map { "${it.source}:${it.outcome}" }}")
    }

    @Test
    fun allSourcesFailingIsReportedNotHidden() {
        var now = 0L
        val dead = FakeReasoner(EvidenceSource.REMOTE_VLM) { null }
        val (machine, orch, events) = harness(listOf(dead), clockStart = { now })
        machine.setInRange(true, 1_000)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 1L, 1_000, EvidenceSource.LOCAL_OCR), 1_000)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 2L, 1_100, EvidenceSource.LOCAL_OCR), 1_100)
        now = 2_000
        val out = orch.forceAnalyze(ByteArray(2), 3L, now, now, "TEST")
        assertFalse("全挂了要如实返回未应用", out.applied)
        assertEquals("NO_SOURCE_ANSWERED", out.reason)
        assertTrue(events.any { it.startsWith("VISION_ALL_FAILED") })
    }

    @Test
    fun visionResultDrivesTheWholeChainToArrival() {
        var now = 1_000L
        val cloud = FakeReasoner(EvidenceSource.REMOTE_VLM) { _ ->
            result(EvidenceSource.REMOTE_VLM,
                doors = listOf("door_1" to intArrayOf(0, 400, 300, 900)),
                relations = listOf(VisionRelation("door_1", RelationOutcome.LIKELY_SAME, 0.85)),
                texts = listOf(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 1L, now, EvidenceSource.REMOTE_VLM)),
                doorType = DoorType.REVOLVING, button = ButtonLocation.LEFT_OF_DOOR,
                scene = "正前方是一个玻璃旋转门")
        }
        val (machine, orch, _) = harness(listOf(cloud), clockStart = { now })
        machine.setInRange(true, now)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 1L, now, EvidenceSource.LOCAL_OCR), now)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 2L, now + 100, EvidenceSource.LOCAL_OCR), now + 100)
        now = 2_500
        val out = orch.tick(ByteArray(2), 3L, now, now)!!
        val r = machine.evaluate(now + 50, emptyList())
        assertTrue("调试：decision=${r.decision} reasons=${r.reasonCodes} doors=${r.candidateCount}" +
            " rel=${out.decisions}", true)
        assertEquals("门候选必须来自视觉结果", 1, r.candidateCount)
        assertEquals("关系确认后注意力要跟过去", "door-1", r.activeDoorId)
        assertEquals(TargetDoorRelationEngine.Verdict.SAME_TARGET, out.decisions.single().verdict)
        assertEquals("地面证据要挂在被确认的那扇上，并带临时来源",
            GroundState.OBSERVED_PROVISIONAL, r.groundState)
        assertEquals(GroundSource.VLM, r.groundSource)
        assertEquals(ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
        assertEquals(DoorType.REVOLVING, machine.doorType())
        assertEquals(ButtonLocation.LEFT_OF_DOOR, machine.buttonLocation())
        assertEquals("正前方是一个玻璃旋转门", machine.sceneSummary())
        assertTrue("候选来源必须写清是 VLM：${machine.candidates().single().source}",
            machine.candidates().single().source == CandidateSource.VLM)
        println("CHAIN 一次视觉调用 → 门/关系/地面/门类型/按钮/摘要 全部到位，decision=${r.decision}")
    }

    @Test
    fun staleVisionRelationCannotKeepConfirmingForever() {
        var now = 1_000L
        val cloud = FakeReasoner(EvidenceSource.REMOTE_VLM) { _ ->
            result(EvidenceSource.REMOTE_VLM,
                texts = listOf(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 1L, now, EvidenceSource.REMOTE_VLM)))
        }
        val (machine, orch, _) = harness(listOf(cloud), clockStart = { now })
        machine.setInRange(true, now)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 1L, now, EvidenceSource.LOCAL_OCR), now)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 2L, now + 100, EvidenceSource.LOCAL_OCR), now + 100)
        now = 2_000
        orch.tick(ByteArray(2), 3L, now, now)
        machine.onGroundEvidence(GroundEvidence(true, false, false, false, 4L, now, "",
            GroundSource.VLM, "S"), null, now)
        assertEquals("刚判完可以到达", ArrivalDecision.ARRIVAL_CONFIRMED, machine.evaluate(now, emptyList()).decision)
        // 人站在那儿不动：目标与门都过期，只有旧的 VLM 关系结论留着 ⇒ 必须重新确认
        now += 10_000
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 40L, now, EvidenceSource.LOCAL_OCR), now)
        machine.onTextHit(TextHit("瑞幸咖啡", 10, 100, 280, 200, 0.9, 41L, now + 100, EvidenceSource.LOCAL_OCR), now + 100)
        machine.onDoorDetected(0, 400, 300, 900, 42L, now + 200, now + 200, CandidateSource.MODEL)
        val later = machine.evaluate(now + 200, emptyList())
        assertNotArrival(later)
        println("STALE 关系过期后 → ${later.decision} ${later.reasonCodes}")
    }

    @Test
    fun sceneQueryBypassesTheTriggerGateButNotTheRateLimit() {
        var now = 0L
        val cloud = FakeReasoner(EvidenceSource.REMOTE_VLM) { _ ->
            result(EvidenceSource.REMOTE_VLM, texts = emptyList(),
                doors = emptyList(), relations = emptyList(), scene = "前方是一排商铺，右侧停着自行车")
        }
        val (machine, orch, _) = harness(listOf(cloud), clockStart = { now })
        machine.setInRange(true, 1_000)
        now = 2_000
        val out = orch.sceneQuery(ByteArray(2), 5L, now, now)
        assertNotNull("用户主动问必须答", out)
        assertEquals("前方是一排商铺，右侧停着自行车", out!!.result?.sceneSummary)
        assertNull("连点两次不能被答两次", orch.sceneQuery(ByteArray(2), 6L, now + 500, now + 500))
        assertNotNull("隔一会儿再问可以", orch.sceneQuery(ByteArray(2), 7L, now + 9_000, now + 9_000))
        assertEquals(2, cloud.calls)
    }

    @Test
    fun replayKeepsItsSourceLabel() {
        val json = """{"ocr_texts":[{"text":"瑞幸咖啡","bbox":[10,100,280,200],"confidence":900}],
 "doors":[{"id":"door_1","bbox":[0,400,300,900],"confidence":800}],
 "relations":[{"door_id":"door_1","relation":"SAME_TARGET","confidence":800}],
 "ground":{"visible":true,"step":null,"curb":null,"obstruction":null},"target_visible":true}"""
        val r = ReplayVisionReasoner(listOf(ReplayVisionReasoner.ReplayFrame("f1", json)))
        val out = r.analyze(null, 77L, 5_000L, profile, VisionContext(profile.poiName))
        assertNotNull(out)
        assertEquals("回放必须一直叫 REPLAY", EvidenceSource.REPLAY, out!!.source)
        assertEquals(77L, out.frameId)
        assertEquals("REPLAY", out.provenance["doors"])
        assertEquals("REPLAY_PROVISIONAL", out.provenance["relations"])
        assertEquals("回放不能带本地 OCR 的来源标记", EvidenceSource.REPLAY, out.texts.firstOrNull()?.source)
        val machine = Last10mStateMachine(profile, sessionId = "S")
        machine.setInRange(true, 5_000)
        val applied = machine.onVisionResult(out, 1080, 1920, 5_000)
        assertEquals(1, applied.newDoors)
        assertEquals("候选来源必须写 REPLAY", CandidateSource.REPLAY,
            machine.candidates().single().source)
    }

    @Test
    fun captureAndDeliverAreSeparateSoLateAnswersCanBeDropped() {
        val cloud = FakeReasoner(EvidenceSource.REMOTE_VLM) { _ -> result(EvidenceSource.REMOTE_VLM) }
        val machine = Last10mStateMachine(profile, sessionId = "S")
        machine.setInRange(true, 1_000)
        val orch = PerceptionOrchestrator(machine, profile, TargetDoorRelationEngine(),
            listOf(cloud), { 5_000L }, 0L, frameSize = { 1080 to 1920 })
        val captured = orch.capture(ByteArray(2), 3L, 5_000L, 5_000L, "TEST")
        assertNotNull(captured)
        assertEquals("capture 不许动状态机", 0, machine.candidates().size)
        orch.deliver(captured!!, 5_000L)
        assertEquals("deliver 才登记", 1, machine.candidates().size)
    }

    private fun assertNotArrival(r: Last10mStateMachine.Result) {
        assertTrue("不能靠旧关系宣布到达：${r.decision}",
            r.decision != ArrivalDecision.ARRIVAL_CONFIRMED)
    }
}
