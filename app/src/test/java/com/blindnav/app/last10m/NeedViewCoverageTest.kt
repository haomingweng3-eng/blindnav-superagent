package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Need-View 全量覆盖回归（任务书第 15 节）。
 *
 * 这一节**不改决策规则**，只把"每个状态都必须有明确的下一步视角、不能出现 null 或
 * 不可能的组合"钉成测试。上一版最要命的现场问题就是：屏幕上什么都不动，
 * 用户和演示者都猜不出系统卡在哪一步。
 *
 * 13 个状态逐个覆盖，并且额外要求：
 *  - `promptFor(view)` 对每个可能的 view 都有非空话术（不能有"该说的时候没话"）；
 *  - 每个 ReasonCode 都有人话解释（面板要念）；
 *  - 方向不可用时必须有原因（PF-07：区分"没接高德"和"ScanCoach 坏了"）。
 */
class NeedViewCoverageTest {

    private fun snap(
        locked: Boolean = true, everLocked: Boolean = true, age: Long = 100,
        doors: Int = 1, active: Boolean = true, relation: Boolean? = true,
        ground: GroundState = GroundState.CLEAR, neighbor: Boolean = false,
        relConflict: Boolean = false, addrConflict: Boolean = false,
        lmConflict: Boolean = false, scan: ScanDirection = ScanDirection.FRONT,
        groundAge: Long = 0, sameSession: Boolean = true,
        groundSource: GroundSource = GroundSource.GROUND_MODEL
    ) = EvidenceSnapshot(locked, everLocked, age, doors, active, relation, ground,
        neighbor, relConflict, addrConflict, lmConflict, scan, groundAge,
        scanReason = if (scan == ScanDirection.UNKNOWN) ScanUnknownReason.ROUTE_BEARING_MISSING
            else ScanUnknownReason.NONE,
        groundSource = groundSource, sameSession = sameSession)

    private data class Case(val name: String, val snapshot: EvidenceSnapshot, val want: NeedView)

    @Test
    fun allFourteenStatesHaveAnExplicitNeedView() {
        val cases = listOf(
            Case("ALL_READY", snap(), NeedView.NONE),
            Case("TARGET_NOT_FOUND/无方向", snap(locked = false, everLocked = false, relation = null,
                ground = GroundState.NOT_OBSERVED, doors = 0, active = false,
                scan = ScanDirection.UNKNOWN), NeedView.SCAN_WIDER),
            Case("TARGET_NOT_FOUND/左", snap(locked = false, everLocked = false, relation = null,
                ground = GroundState.NOT_OBSERVED, doors = 0, active = false,
                scan = ScanDirection.LEFT), NeedView.SCAN_LEFT),
            Case("TARGET_NOT_FOUND/右", snap(locked = false, everLocked = false, relation = null,
                ground = GroundState.NOT_OBSERVED, doors = 0, active = false,
                scan = ScanDirection.RIGHT), NeedView.SCAN_RIGHT),
            Case("TARGET_NOT_FOUND/正前", snap(locked = false, everLocked = false, relation = null,
                ground = GroundState.NOT_OBSERVED, doors = 0, active = false), NeedView.LOOK_UP_SIGN),
            Case("TARGET_UNSTABLE", snap(age = 1_500), NeedView.NONE),
            Case("TARGET_LOST", snap(locked = false, everLocked = true), NeedView.LOOK_UP_SIGN),
            Case("DOOR_NOT_FOUND", snap(doors = 0, active = false, relation = null,
                ground = GroundState.NOT_OBSERVED), NeedView.LOOK_AT_DOOR),
            Case("MULTIPLE_DOORS", snap(doors = 3, relation = null), NeedView.SCAN_WIDER),
            Case("RELATION_UNKNOWN", snap(relation = null), NeedView.LOOK_AT_DOOR),
            Case("RELATION_CONFLICT", snap(relConflict = true), NeedView.REOBSERVE_TARGET),
            Case("GROUND_NOT_VISIBLE", snap(ground = GroundState.NOT_OBSERVED), NeedView.LOOK_DOWN_GROUND),
            Case("GROUND_UNKNOWN", snap(ground = GroundState.UNKNOWN), NeedView.LOOK_DOWN_GROUND),
            Case("GROUND_STALE", snap(ground = GroundState.OBSERVED_MANUAL, groundAge = 9_000,
                groundSource = GroundSource.DEBUG_MANUAL), NeedView.LOOK_DOWN_GROUND),
            Case("GROUND_OBSTRUCTED", snap(ground = GroundState.BLOCKED), NeedView.LOOK_DOWN_GROUND),
            Case("ADDRESS_CONFLICT", snap(addrConflict = true), NeedView.REOBSERVE_TARGET),
            Case("LANDMARK_CONFLICT", snap(lmConflict = true), NeedView.REOBSERVE_TARGET),
            Case("SESSION_MISMATCH", snap(sameSession = false), NeedView.REOBSERVE_TARGET)
        )
        val viewsSeen = LinkedHashSet<NeedView>()
        for (c in cases) {
            val plan = NeedViewPlanner.plan(c.snapshot)
            assertNotNull("${c.name} 必须有 plan", plan)
            assertEquals("${c.name} 的 needView 不对（实得 ${plan.view}，理由 ${plan.reasons}）",
                c.want, plan.view)
            assertTrue("${c.name} 的话术不能是空串", plan.prompt.isNotBlank())
            assertTrue("${c.name} 必须有至少一个 reason", plan.reasons.isNotEmpty())
            viewsSeen.add(plan.view)
            println("  ${c.name.padEnd(22)} → ${plan.view.name.padEnd(18)} reasons=${plan.reasons} 「${plan.prompt}」")
        }
        // 方向不可用的原因必须能从快照里读出来，而不是靠猜"是不是 ScanCoach 坏了"
        val noBearing = snap(scan = ScanDirection.UNKNOWN, locked = false, everLocked = false)
        assertEquals("没接高德要显示 ROUTE_BEARING_MISSING",
            ScanUnknownReason.ROUTE_BEARING_MISSING, noBearing.scanReason)
        assertTrue("面板话术里要点名原因不是模型坏了",
            NeedViewPlanner.describe(noBearing.scanReason).contains("非 ScanCoach 故障"))
        assertTrue("18 个状态至少覆盖 6 种不同视角指令：${viewsSeen.size}", viewsSeen.size >= 6)
    }

    /**
     * 这一条是今晚真跑 Demo 时抓出来的措辞漏洞，单独钉住：
     * 眼前只有一扇门、而这扇已经被判成"隔壁"时，规划器原本会走到最后的 NONE，
     * 于是话术变成"入口已确认"——decision 是 RELATION_CONFLICT，嘴上却在确认入口。
     */
    @Test
    fun singleDoorJudgedNeighbourNeverProducesAnArrivalPrompt() {
        val neighbour = NeedViewPlanner.plan(snap(relation = false, ground = GroundState.CLEAR))
        assertEquals("被判隔壁的单门要的是重看关系：${neighbour.view}",
            NeedView.REOBSERVE_TARGET, neighbour.view)
        assertTrue("话术不能是确认到达：「${neighbour.prompt}」",
            !neighbour.prompt.contains("已确认"))
        assertTrue("必须有 RELATION_CONFLICT 理由：${neighbour.reasons}",
            neighbour.reasons.contains(ReasonCode.RELATION_CONFLICT))
        // 多扇里有一扇被判隔壁：仍然是"先左右扫"，不能催用户往下看地面
        val many = NeedViewPlanner.plan(snap(relation = false, doors = 2, neighbor = true))
        assertEquals(NeedView.SCAN_WIDER, many.view)
        println("  隔壁单门 → ${neighbour.view}「${neighbour.prompt}」 reasons=${neighbour.reasons}")
    }

    @Test
    fun everyReasonCodeHasPlainLanguageAndMatchesSnapshotFields() {        val missing = ReasonCode.values().filter {
            it != ReasonCode.NONE && NeedViewPlanner.describe(it).length < 4
        }
        assertTrue("每个 reason 都要能翻成人话：$missing", missing.isEmpty())
        val unknownMissing = ScanUnknownReason.values().filter {
            it != ScanUnknownReason.NONE && NeedViewPlanner.describe(it).length < 4
        }
        assertTrue("方向不可用也要能解释：$unknownMissing", unknownMissing.isEmpty())
        println("  ReasonCode=${ReasonCode.values().size} 个，ScanUnknownReason=${ScanUnknownReason.values().size} 个，全部有中文解释")
    }

    @Test
    fun manualGroundIsNeverASignThatSomethingIsMissingOrSafe() {
        // 人工声明地面已看：不该再催"拍地面"，也不该被显示成"安全"
        val manual = NeedViewPlanner.plan(snap(ground = GroundState.OBSERVED_MANUAL,
            groundSource = GroundSource.DEBUG_MANUAL))
        assertEquals(NeedView.NONE, manual.view)
        assertTrue("人工地面不能产生 SAFE 说法：${manual.prompt}", !manual.prompt.contains("安全"))
        // 但过期了就必须重新看
        val stale = NeedViewPlanner.plan(snap(ground = GroundState.OBSERVED_MANUAL,
            groundSource = GroundSource.DEBUG_MANUAL, groundAge = 5_000))
        assertEquals(NeedView.LOOK_DOWN_GROUND, stale.view)
        assertTrue(stale.reasons.contains(ReasonCode.GROUND_STALE))
        println("  OBSERVED_MANUAL 新鲜=${manual.view} 过期=${stale.view}/${stale.reasons}")
    }

    @Test
    fun conflictAlwaysOutranksMissingEvidence() {
        val plan = NeedViewPlanner.plan(snap(addrConflict = true, neighbor = true, relConflict = true,
            doors = 4, relation = null, ground = GroundState.NOT_OBSERVED))
        assertEquals(NeedView.REOBSERVE_TARGET, plan.view)
        assertEquals("冲突必须排在最前面", ReasonCode.ADDRESS_CONFLICT, plan.reasons.first())
        println("  冲突优先：${plan.reasons}")
    }

    @Test
    fun noStateIsLeftWithoutAPrompt() {
        // 遍历 planner 能到达的每个 view：都必须有话术，且不含移动指令/安全承诺
        val snapshots = listOf(
            snap(), snap(locked = false, everLocked = false), snap(age = 1_500),
            snap(locked = false, everLocked = true), snap(doors = 0, active = false, relation = null),
            snap(doors = 3, relation = null), snap(relation = null), snap(relConflict = true),
            snap(ground = GroundState.NOT_OBSERVED), snap(ground = GroundState.UNKNOWN),
            snap(ground = GroundState.BLOCKED), snap(addrConflict = true), snap(lmConflict = true),
            snap(scan = ScanDirection.LEFT, locked = false, everLocked = false, doors = 0,
                active = false, relation = null),
            snap(scan = ScanDirection.RIGHT, locked = false, everLocked = false, doors = 0,
                active = false, relation = null)
        )
        val views = LinkedHashSet<NeedView>()
        for (s in snapshots) {
            val p = NeedViewPlanner.plan(s)
            views.add(p.view)
            assertTrue("「${p.view}」话术为空", p.prompt.isNotBlank())
            assertTrue("话术里不许出现移动指令：${p.prompt}",
                !p.prompt.contains("往左走") && !p.prompt.contains("往右走") &&
                    !p.prompt.contains("向前走") && !p.prompt.contains("米"))
            assertTrue("话术里不许承诺安全：${p.prompt}", !p.prompt.contains("安全"))
        }
        println("  覆盖到 view=${views.sorted()}，共 ${views.size} 种，全部有合规话术")
        assertTrue("至少覆盖 6 种不同视角指令", views.size >= 6)
    }
}
