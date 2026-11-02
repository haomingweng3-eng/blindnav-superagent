package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 人工入口候选（PF-04）+ 入口跟踪压力测试（任务书第 4/16 节）。
 *
 * 为什么要专门测这一条：上一版把**研究面板自己的宽高**当画面尺寸用，
 * 于是"点哪儿都是同一个框"，`EntranceTracker` 永远归并成同一扇 `door-1`，
 * `candidates` 恒为 1 —— 邻门冲突与 `MULTIPLE_DOORS` 这两条最该在门口拦住系统的路径
 * 在真机上根本走不到。这一版换成分区按钮后，这些路径必须**在测试里真的被走到**。
 */
class DebugDoorAndTrackerTest {

    private val W = 1080
    private val H = 1920

    private fun machine() = Last10mStateMachine(TargetPoiProfile.build("瑞幸咖啡"), sessionId = "S1").also {
        it.setInRange(true, 1_000)
    }

    private fun hit(text: String, frame: Long, ts: Long) =
        TextHit(text, 10, 10, 120, 40, 0.9, frame, ts, EvidenceSource.OCR)

    private fun lockTarget(m: Last10mStateMachine) {
        m.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        m.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
    }

    // ---------------------------------------------------------------- 几何
    @Test
    fun debugBoxesUseRealFrameSizeAndDoNotOverlap() {
        val left = DebugDoorProposal.box(DebugDoorPosition.LEFT, W, H)!!
        val center = DebugDoorProposal.box(DebugDoorPosition.CENTER, W, H)!!
        val right = DebugDoorProposal.box(DebugDoorPosition.RIGHT, W, H)!!
        println("  LEFT=$left CENTER=$center RIGHT=$right  (frame ${W}x$H)")
        assertEquals("LEFT 起点 = 0.05*1080", 54, left[0])
        assertEquals("LEFT 终点 = 0.35*1080", 378, left[2])
        assertEquals("CENTER 起点 = 0.35*1080", 378, center[0])
        assertEquals("RIGHT 终点 = 0.95*1080", 1026, right[2])
        assertEquals("竖直取景带 0.40*1920", 768, left[1])
        assertEquals("竖直取景带 0.90*1920", 1728, left[3])
        // 三个分区两两不重叠，这是"两个按钮 = 两个候选门"的几何前提
        assertTrue(iou(left, center) == 0.0)
        assertTrue(iou(center, right) == 0.0)
        assertTrue(iou(left, right) == 0.0)
        // 尺寸不可用必须返回 null，不能退化成一个假想的默认尺寸
        assertNull(DebugDoorProposal.box(DebugDoorPosition.LEFT, 0, 0))
        assertNull(DebugDoorProposal.box(DebugDoorPosition.LEFT, -1, 100))
    }

    private fun iou(a: IntArray, b: IntArray): Double {
        val ix = (minOf(a[2], b[2]) - maxOf(a[0], b[0])).coerceAtLeast(0)
        val iy = (minOf(a[3], b[3]) - maxOf(a[1], b[1])).coerceAtLeast(0)
        val inter = (ix * iy).toDouble()
        val areaA = ((a[2] - a[0]) * (a[3] - a[1])).toDouble()
        val areaB = ((b[2] - b[0]) * (b[3] - b[1])).toDouble()
        val union = areaA + areaB - inter
        return if (union <= 0) 0.0 else inter / union
    }

    // ---------------------------------------------------------------- 多门路径
    @Test
    fun leftAndRightProduceTwoDistinctCandidatesAndReachNeighborConflict() {
        val m = machine()
        lockTarget(m)
        val r1 = m.onDebugDoor(DebugDoorPosition.LEFT, W, H, 100, 1_300, 1_300)
        assertEquals("左门先成一个候选", 1, r1.candidateCount)
        val r2 = m.onDebugDoor(DebugDoorPosition.RIGHT, W, H, 101, 1_400, 1_400)
        assertTrue("左右两扇必须得到两个候选（实测 ${r2.candidateCount}）", r2.candidateCount >= 2)
        assertTrue("邻门冲突必须真的成立", m.neighborConflict(1_400))
        assertTrue("reasonCodes 必须含 MULTIPLE_DOORS", r2.reasonCodes.contains(ReasonCode.MULTIPLE_DOORS))
        assertTrue("reasonCodes 必须含 NEIGHBOR_CONFLICT", r2.reasonCodes.contains(ReasonCode.NEIGHBOR_CONFLICT))
        assertEquals("多门且未判关系 → 先扫开", NeedView.SCAN_WIDER, r2.needView)
        assertEquals(ArrivalDecision.REOBSERVE_DOOR, r2.decision)
        assertNot("邻门未澄清时绝不能到达", ArrivalDecision.ARRIVAL_CONFIRMED, r2.decision)
        val doors = m.candidates()
        println("  candidates=${doors.map { "${it.doorId}/${it.debugPosition}/${it.source}" }}")
        assertEquals("两个候选必须带不同 debugPosition",
            setOf(DebugDoorPosition.LEFT, DebugDoorPosition.RIGHT), doors.mapNotNull { it.debugPosition }.toSet())
        assertTrue("候选必须写明是人工产生的",
            doors.all { it.source == CandidateSource.DEBUG_MANUAL_PROPOSAL })
        assertTrue("候选必须带来源帧", doors.all { it.sourceFrameId in 100L..101L })
        assertTrue("候选必须带会话身份", doors.all { it.sessionId == "S1" })
    }

    @Test
    fun samePositionAcrossFramesMergesIntoOneDoor() {
        val m = machine()
        lockTarget(m)
        var last: Last10mStateMachine.Result? = null
        for (f in 0 until 5) last = m.onDebugDoor(DebugDoorPosition.CENTER, W, H, 200L + f, 1_300L + f * 50, 1_300L + f * 50)
        assertEquals("同一分区反复标注必须归并成同一扇门", 1, last!!.candidateCount)
        assertEquals("seenFrames 应累计", 5, m.candidates().single().seenFrames)
    }

    @Test
    fun stateRecoversAfterOneDoorExpires() {
        val m = machine()
        lockTarget(m)
        m.onDebugDoor(DebugDoorPosition.LEFT, W, H, 100, 1_300, 1_300)
        m.onDebugDoor(DebugDoorPosition.RIGHT, W, H, 101, 1_400, 1_400)
        assertEquals(2, m.candidates().size)
        // 先让邻门判据过期（1500ms）：多门冲突不再成立，但候选还在
        val mid = m.evaluate(3_200, emptyList())
        assertFalse("两帧都不新鲜后不该还算邻门冲突", m.neighborConflict(3_200))
        assertTrue("候选仍未澄清，保持多门理由或关系未判",
            mid.reasonCodes.contains(ReasonCode.MULTIPLE_DOORS) ||
                mid.reasonCodes.contains(ReasonCode.RELATION_UNKNOWN))
        // 再让整组过期（prune 默认 6000ms），只剩最新标注的那一扇
        val after = m.onDebugDoor(DebugDoorPosition.LEFT, W, H, 300, 8_000, 8_000)
        assertEquals("旧候选必须被清掉，只剩一个", 1, after.candidateCount)
        assertEquals(DebugDoorPosition.LEFT, m.candidates().single().debugPosition)
        println("  过期恢复：mid=${mid.decision}/${mid.reasonCodes} → after=${after.decision}/${after.reasonCodes}")
    }

    // ---------------------------------------------------------------- §16 压力
    @Test
    fun jitterOverTwentyFramesKeepsOneTrackId() {
        val t = EntranceTracker()
        var ids = LinkedHashSet<String>()
        var x = 300
        for (f in 0 until 20) {
            x += if (f % 2 == 0) 8 else -6        // 轻微抖动
            ids.add(t.observeDoor(x, 400, x + 200, 700, 100L + f, 1_000L + f * 60, 1_000L + f * 60,
                CandidateSource.MODEL))
        }
        println("  20 帧抖动轨迹 id=$ids")
        assertEquals("同一扇门轻微抖动必须保持同一 ID", 1, ids.size)
        assertEquals(20, t.candidates().single().seenFrames)
    }

    @Test
    fun twoDoorsSideBySideMustNotMerge() {
        val t = EntranceTracker()
        val a = t.observeDoor(100, 400, 260, 700, 1, 1_000, 1_000, CandidateSource.MODEL)
        val b = t.observeDoor(700, 400, 860, 700, 2, 1_050, 1_050, CandidateSource.MODEL)
        assertNot("隔壁门不能并成同一扇", a, b)
        assertEquals(2, t.candidates().size)
        assertTrue(t.neighborConflict(1_050))
        println("  并排两门 → $a / $b，邻门冲突=${t.neighborConflict(1_050)}")
    }

    @Test
    fun staleDoorIsPrunedAndReported() {
        val t = EntranceTracker()
        val a = t.observeDoor(100, 400, 260, 700, 1, 1_000, 1_000, CandidateSource.MODEL)
        val dropped = t.prune(9_000)              // ttl 默认 6000ms
        assertEquals("超时候选必须被丢弃", listOf(a), dropped)
        assertTrue(t.candidates().isEmpty())
        assertNull("活跃候选也必须跟着清掉", t.active())
    }

    @Test
    fun doorBDoesNotInheritDoorARelation() {
        val t = EntranceTracker()
        val a = t.observeDoor(100, 400, 260, 700, 1, 1_000, 1_000, CandidateSource.MODEL)
        t.confirmRelation(a, EvidenceSource.VLM, true)
        // A 离开画面（超过 gap 帧/时间），B 出现
        val b = t.observeDoor(800, 400, 960, 700, 30, 5_000, 5_000, CandidateSource.MODEL)
        assertNot(a, b)
        val doorB = t.candidates().first { it.doorId == b }
        assertNull("B 不能继承 A 的关系结论", doorB.relationConfirmed)
        // 关系作废后，A 的确认记录也不能再被当成"已确认的门存在"
        t.invalidateRelations("target_lock_expired")
        assertFalse("作废后不该还有已确认的门",
            t.candidates().any { it.relationConfirmed == true })
        assertTrue(t.relationConflict(t.active()?.doorId) == false)
        println("  A=$a 已确认 → B=$b relation=${doorB.relationConfirmed}，作废后 candidates=${t.candidates().size}")
    }

    @Test
    fun frameIdRewindCreatesNewTrackAndNeverLooksFresh() {
        val t = EntranceTracker()
        val a = t.observeDoor(100, 400, 260, 700, 50, 5_000, 5_000, CandidateSource.MODEL)
        // 帧号倒退（重新打开相机、或另一条帧流）——不能并到旧门上去
        val back = t.observeDoor(100, 400, 260, 700, 10, 5_100, 5_100, CandidateSource.MODEL)
        assertNot("帧号倒退不能归并到旧门", a, back)
        // 时间戳在未来（时钟回退造成的假"新鲜"）必须被 ageMs 判成已过期
        val future = t.observeDoor(600, 400, 760, 700, 60, 9_000, 5_100, CandidateSource.MODEL)
        val door = t.candidates().first { it.doorId == future }
        assertEquals("未来时间戳的龄必须是 MAX", Long.MAX_VALUE, door.ageMs(5_100))
        println("  帧号倒退 → 新 id=$back；未来时间戳 → age=${door.ageMs(5_100)}")
    }

    @Test
    fun candidateSourceIsAlwaysRecorded() {
        val m = machine()
        lockTarget(m)
        m.onDebugDoor(DebugDoorPosition.CENTER, W, H, 100, 1_300, 1_300)
        val manual = m.candidates().single()
        assertEquals(CandidateSource.DEBUG_MANUAL_PROPOSAL, manual.source)
        assertEquals(DebugDoorPosition.CENTER, manual.debugPosition)
        // 模型给出的候选走另一条入口，来源必须能区分开
        m.onDoorDetected(50, 400, 200, 700, 101, 1_400, 1_400, CandidateSource.MODEL)
        val bySource = m.candidates().associate { it.doorId to it.source }
        println("  来源分布=$bySource")
        assertTrue("同一台机器里必须能同时存在两种来源",
            bySource.values.containsAll(setOf(CandidateSource.DEBUG_MANUAL_PROPOSAL, CandidateSource.MODEL)))
    }

    private fun assertNot(message: String, a: Any?, b: Any?) {
        assertFalse("$message（两边都是 $a）", a == b)
    }

    private fun assertNot(a: Any?, b: Any?) = assertNot("两者不应相等", a, b)

    // ---------------------------------------------------------------- 地面语义（§12）
    @Test
    fun manualGroundIsObservedNotSafe() {
        val m = machine()
        lockTarget(m)
        m.onDebugDoor(DebugDoorPosition.CENTER, W, H, 100, 1_300, 1_300)
        val r = m.onGroundEvidence(GroundEvidence(true, false, false, false, 101, 1_400,
            "人工声明", GroundSource.DEBUG_MANUAL, "S1"), null, 1_400)
        assertEquals("人工声明只能得到 OBSERVED_MANUAL，不是 SAFE/CLEAR",
            GroundState.OBSERVED_MANUAL, r.groundState)
        assertEquals(GroundSource.DEBUG_MANUAL, r.groundSource)
        assertNot("人工地面证据不能算成模型确认", GroundState.CLEAR, r.groundState)
        // 模型来源才允许 CLEAR
        val m2 = machine(); lockTarget(m2)
        m2.onDebugDoor(DebugDoorPosition.CENTER, W, H, 100, 1_300, 1_300)
        val r2 = m2.onGroundEvidence(GroundEvidence(true, false, false, false, 101, 1_400,
            "模型", GroundSource.GROUND_MODEL, "S1"), null, 1_400)
        assertEquals(GroundState.CLEAR, r2.groundState)
        println("  人工地面=${r.groundState} / 模型地面=${r2.groundState}")
    }

    @Test
    fun groundStateOfIsTheSingleSourceOfTruth() {
        assertEquals(GroundState.NOT_OBSERVED, groundStateOf(null))
        assertEquals(GroundState.UNKNOWN, groundStateOf(
            GroundEvidence(false, false, false, false, 1, 1_000)))
        assertEquals(GroundState.BLOCKED, groundStateOf(
            GroundEvidence(true, false, false, true, 1, 1_000, "", GroundSource.GROUND_MODEL)))
        assertEquals(GroundState.OBSERVED_MANUAL, groundStateOf(
            GroundEvidence(true, false, false, false, 1, 1_000, "", GroundSource.DEBUG_MANUAL)))
        assertEquals(GroundState.CLEAR, groundStateOf(
            GroundEvidence(true, false, false, false, 1, 1_000, "", GroundSource.GROUND_MODEL)))
        // visible=false 时其它字段一律无意义，不能因为 step/curb 都是 false 就变成"安全"
        assertEquals("没看见就是 UNKNOWN，不等于 SAFE", GroundState.UNKNOWN,
            groundStateOf(GroundEvidence(false, false, false, false, 1, 1_000, "", GroundSource.DEBUG_MANUAL)))
    }
}
