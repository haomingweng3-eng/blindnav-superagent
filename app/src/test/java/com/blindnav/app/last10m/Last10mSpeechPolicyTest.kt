package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 最后十米语音裁决（任务书第 3 节 / PF-02 + 第 17 节联合污染）。
 *
 * 要防的是两种对称的失败：
 *  - 直接 `speakPrompts = true`：OCR 3 帧/秒，用户站在门口被同一句话淹死；
 *  - 保持 `false`：视障用户什么都听不到，"最后十米"对使用者没有输出面。
 *
 * 断言方向而不是具体措辞：同一语义只能播一次、变化了必须播、
 * STOP 期间 P1 必须让路、ARRIVAL 每会话一次、环境描述不许盖过关键提示、
 * 以及最要紧的一条——**跨帧污染的证据不能被念成"入口已确认"**。
 */
class Last10mSpeechPolicyTest {

    private fun signal(
        cue: SpeechCue = SpeechCue.KEEP_SEARCHING,
        text: String = "继续找目标招牌",
        state: Last10mState = Last10mState.SEARCH_TARGET,
        decision: ArrivalDecision = ArrivalDecision.KEEP_SEARCHING,
        needView: NeedView = NeedView.SCAN_WIDER,
        targetLocked: Boolean = false,
        relationConflict: Boolean = false,
        neighborConflict: Boolean = false,
        ground: GroundState = GroundState.NOT_OBSERVED,
        doors: Int = 0,
        session: String? = "S1",
        priority: Last10mPriority = Last10mPriority.LAST10M
    ) = Last10mSignal(state, decision, needView, cue, text, targetLocked,
        relationConflict, neighborConflict, ground, doors, session, priority)

    // ---------------------------------------------------------------- Speech A
    @Test
    fun hundredIdenticalFramesProduceAtMostOneUtterance() {
        val policy = Last10mSpeechPolicy()
        var spoken = 0
        val perCue = HashMap<String, Int>()
        for (i in 0 until 100) {
            val v = policy.decide(signal(), i * 10L)     // 1000ms 内的 100 个相同帧
            if (v is SpeechVerdict.Speak) {
                spoken++
                perCue[v.cue.name] = (perCue[v.cue.name] ?: 0) + 1
            }
        }
        println("SPEECH A 100 个相同 OCR 帧 → 播 $spoken 条，按 cue=$perCue，压掉 ${policy.suppressed} 条")
        assertTrue("同一语义 100 帧最多播一条（实测 spoken=$spoken）", spoken <= 1)
        assertEquals("一条都不播说明没接通", 1, spoken)
    }

    // ---------------------------------------------------------------- Speech B
    @Test
    fun needViewChangeProducesNewPrompt() {
        val policy = Last10mSpeechPolicy()
        val first = policy.decide(signal(), 0L)
        assertTrue("第一次应当播", first is SpeechVerdict.Speak)
        // 门出现了 → needView 变成 LOOK_AT_DOOR，必须换一条新的
        val second = policy.decide(signal(
            cue = SpeechCue.LOOK_AT_DOOR, text = "看到目标招牌，请顺着招牌下方找入口",
            state = Last10mState.SEARCH_ENTRANCE, needView = NeedView.LOOK_AT_DOOR, doors = 1), 400L)
        assertTrue("needView 变了必须播（reason=${second.reason}）", second is SpeechVerdict.Speak)
        assertEquals(SpeechCue.LOOK_AT_DOOR, (second as SpeechVerdict.Speak).cue)
        println("SPEECH B needView 改变 → 播「${second.text}」")
    }

    @Test
    fun sameCueAfterDifferentCueIsStillCooldownBlocked() {
        // 变化了但说的是同一句语义，2 秒内不重复念。
        val policy = Last10mSpeechPolicy()
        policy.decide(signal(cue = SpeechCue.LOOK_DOWN_GROUND, text = "请稍微向下拍摄门前区域",
            state = Last10mState.VERIFY_GROUND, needView = NeedView.LOOK_DOWN_GROUND,
            doors = 1), 0L)
        val back = policy.decide(signal(cue = SpeechCue.LOOK_AT_DOOR, text = "看到目标招牌，请顺着招牌下方找入口",
            state = Last10mState.SEARCH_ENTRANCE, needView = NeedView.LOOK_AT_DOOR, doors = 1), 300L)
        assertTrue("换了语义就播", back is SpeechVerdict.Speak)
        val again = policy.decide(signal(cue = SpeechCue.LOOK_DOWN_GROUND, text = "请稍微向下拍摄门前区域",
            state = Last10mState.VERIFY_GROUND, needView = NeedView.LOOK_DOWN_GROUND, doors = 2), 500L)
        assertTrue("同一 cue 冷却 2000ms 内不重复（reason=${again.reason}）", again is SpeechVerdict.Suppress)
        println("SPEECH B2 同 cue 冷却 → ${(again as SpeechVerdict.Suppress).reason}")
    }

    // ---------------------------------------------------------------- Speech C
    @Test
    fun arrivalIsAnnouncedOncePerSession() {
        val policy = Last10mSpeechPolicy()
        var spoken = 0
        val machine = Last10mStateMachine(TargetPoiProfile.build("瑞幸咖啡"), sessionId = "S1")
        machine.setInRange(true, 1_000)
        policy.decide(Last10mSpeechCue.signal(machine.evaluate(1_000, emptyList()), "S1"), 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        machine.onDoorDetected(10, 100, 90, 200, 3, 1_300, 1_300)
        machine.onVlmFacts(facts(RelationOutcome.LIKELY_SAME, 4, 1_400), 1_400)
        val r = machine.onGroundEvidence(
            GroundEvidence(true, false, false, false, 5, 1_500, "人工声明",
                GroundSource.DEBUG_MANUAL, "S1"), null, 1_500)
        assertEquals("前置条件齐备应当到达", ArrivalDecision.ARRIVAL_CONFIRMED, r.decision)
        for (t in 0 until 20) {
            val again = machine.evaluate(1_500L + t * 30, emptyList())
            val v = policy.decide(Last10mSpeechCue.signal(again, "S1"), 1_500L + t * 30)
            if (v is SpeechVerdict.Speak && v.cue == SpeechCue.ARRIVAL_CONFIRMED) spoken++
        }
        assertEquals("ARRIVAL 每个会话最多播一次（实际 $spoken）", 1, spoken)
        println("SPEECH C ARRIVAL 连续 20 次 evaluate → 播 $spoken 条")
    }

    // ---------------------------------------------------------------- Speech D
    @Test
    fun stopPriorityOutranksLast10m() {
        val policy = Last10mSpeechPolicy()
        val ok = policy.decide(signal(), 0L)
        assertTrue(ok is SpeechVerdict.Speak)
        // STOP 级播报开始（障碍/停下）
        policy.noteHigherPriority(100L)
        val blocked = policy.decide(signal(
            cue = SpeechCue.LOOK_AT_DOOR, text = "看到目标招牌，请顺着招牌下方找入口",
            state = Last10mState.SEARCH_ENTRANCE, needView = NeedView.LOOK_AT_DOOR, doors = 1), 200L)
        assertTrue("STOP 期间 P1 必须让路（reason=${blocked.reason}）", blocked is SpeechVerdict.Suppress)
        assertEquals("stop_has_priority", (blocked as SpeechVerdict.Suppress).reason)
        // 3 秒窗口过后，同样的变化才允许播
        val later = policy.decide(signal(
            cue = SpeechCue.LOOK_DOWN_GROUND, text = "请稍微向下拍摄门前区域",
            state = Last10mState.VERIFY_GROUND, needView = NeedView.LOOK_DOWN_GROUND, doors = 1), 4_000L)
        assertTrue("STOP 窗口结束后 P1 恢复（reason=${later.reason}）", later is SpeechVerdict.Speak)
        println("SPEECH D STOP→suppress，窗口后恢复；中间挡掉 ${(blocked as SpeechVerdict.Suppress).reason}")
    }

    @Test
    fun priorityOrderIsNeverInvertible() {
        // 高优先级永远不许被低优先级打断；反向可以。
        val p = Last10mSpeechPolicy()
        assertFalse(p.canPreempt(Last10mPriority.STOP, Last10mPriority.LAST10M))
        assertFalse(p.canPreempt(Last10mPriority.LAST10M, Last10mPriority.SCENE))
        assertTrue(p.canPreempt(Last10mPriority.SCENE, Last10mPriority.LAST10M))
        assertTrue(p.canPreempt(Last10mPriority.NAVIGATION, Last10mPriority.STOP))
        // 声明顺序就是优先级
        assertEquals(0, Last10mPriority.STOP.ordinal)
        assertEquals(1, Last10mPriority.LAST10M.ordinal)
        assertEquals(3, Last10mPriority.SCENE.ordinal)
    }

    // ---------------------------------------------------------------- Speech E
    @Test
    fun sceneDescriptionCannotOverrideLast10mKeyPrompt() {
        val policy = Last10mSpeechPolicy()
        val arrival = policy.decide(signal(
            cue = SpeechCue.ARRIVAL_CONFIRMED, text = "入口已确认",
            state = Last10mState.ARRIVAL_CONFIRMED, decision = ArrivalDecision.ARRIVAL_CONFIRMED,
            needView = NeedView.NONE, targetLocked = true, doors = 1,
            ground = GroundState.OBSERVED_MANUAL), 0L)
        assertTrue("到达应当播", arrival is SpeechVerdict.Speak)
        // 环境描述（P3）想插进来
        val scene = policy.decide(signal(
            cue = SpeechCue.KEEP_SEARCHING, text = "左边有一根立柱",
            priority = Last10mPriority.SCENE), 500L)
        assertTrue("环境描述不许盖过最后十米的关键提示（reason=${scene.reason}）",
            scene is SpeechVerdict.Suppress)
        assertFalse("ARRIVAL 之后一段时间内环境描述保持静音", policy.sceneAllowed(3_000L))
        assertTrue("冷却窗过后环境描述可以走自己的策略", policy.sceneAllowed(60_000L))
        println("SPEECH E 环境描述被挡：${(scene as SpeechVerdict.Suppress).reason}")
    }

    // ---------------------------------------------------------------- 措辞安全
    @Test
    fun safetyWordingIsNeverSpoken() {
        val policy = Last10mSpeechPolicy()
        for (text in listOf("门前安全，可以通过", "入口已确认，安全", "往左边走两步", "还有 3 米")) {
            val v = policy.decide(signal(cue = SpeechCue.ARRIVAL_CONFIRMED, text = text), 0L)
            assertTrue("「$text」必须被措辞闸拦下", v is SpeechVerdict.Suppress)
            assertTrue((v as SpeechVerdict.Suppress).reason.startsWith("forbidden_phrasing"))
        }
        println("SPEECH 措辞闸：4 条越权说法全部拦下")
    }

    @Test
    fun manualGroundNeverClaimsSafe() {
        // §12：人工声明的地面只能说"已记录"，不能说"安全"。
        val policy = Last10mSpeechPolicy()
        val v = policy.decide(signal(
            cue = SpeechCue.GROUND_RECORDED, text = "门前区域已记录。",
            state = Last10mState.VERIFY_GROUND, ground = GroundState.OBSERVED_MANUAL), 0L)
        assertTrue("如实措辞允许播", v is SpeechVerdict.Speak)
        assertFalse("人工声明的地面不许说安全", v.text.contains("安全"))
    }

    // ---------------------------------------------------------------- §17 联合污染
    @Test
    fun expiredTargetCannotBeAnnouncedAsFoundEntrance() {
        // Frame A：目标锁定 → 播"看到目标"；随后目标过期；Frame B：出现另一扇门。
        // 要求：系统不能继续播"入口已找到/入口已确认"。
        val machine = Last10mStateMachine(TargetPoiProfile.build("瑞幸咖啡"), sessionId = "S1")
        val policy = Last10mSpeechPolicy()
        machine.setInRange(true, 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        val locked = machine.evaluate(1_300, listOf("瑞幸咖啡"))
        policy.decide(Last10mSpeechCue.signal(locked, "S1"), 1_300)

        // 目标离开画面 6 秒（超过 lostTimeout），锁失效
        val lost = machine.evaluate(7_000, emptyList())
        assertFalse("前提：锁必须已经失效", lost.targetLocked)
        policy.decide(Last10mSpeechCue.signal(lost, "S1", targetJustLost = true), 7_000)

        // 又看到一扇门（可能是隔壁的）
        val door = machine.onDoorDetected(500, 100, 600, 260, 30, 7_500, 7_500)
        val v = policy.decide(Last10mSpeechCue.signal(door, "S1"), 7_500)
        assertNotConfirmed(v)
        println("§17-1 旧目标 + 新门 → decision=${door.decision} 语音=${v.reason} text=\"${v.text}\"")
        for (t in 7_600L..12_000L step 200) {
            assertNotConfirmed(policy.decide(
                Last10mSpeechCue.signal(machine.evaluate(t, emptyList()), "S1"), t))
        }
    }

    @Test
    fun staleRelationCannotBeAnnouncedAsArrival() {
        // Frame C：人工声明地面已看（DEBUG_MANUAL）；随后关系证据过期。
        // 要求：不能播"入口已确认"。
        val machine = Last10mStateMachine(TargetPoiProfile.build("瑞幸咖啡"), sessionId = "S1")
        val policy = Last10mSpeechPolicy()
        machine.setInRange(true, 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        machine.onDoorDetected(10, 100, 90, 200, 3, 1_300, 1_300)
        machine.onVlmFacts(facts(RelationOutcome.LIKELY_SAME, 4, 1_400), 1_400)
        machine.onGroundEvidence(GroundEvidence(true, false, false, false, 5, 1_500, "人工声明",
            GroundSource.DEBUG_MANUAL, "S1"), null, 1_500)
        val first = machine.evaluate(1_500, emptyList())
        assertEquals("前提：此时确实齐备可以到达", ArrivalDecision.ARRIVAL_CONFIRMED, first.decision)
        val spokenArrival = policy.decide(Last10mSpeechCue.signal(first, "S1"), 1_500)
        assertTrue("第一次到达要播", spokenArrival is SpeechVerdict.Speak)

        // 目标证据与 VLM 关系一起过期（人不说话了，但门还在画面里）
        val later = machine.evaluate(20_000, emptyList())
        val v = policy.decide(Last10mSpeechCue.signal(later, "S1"), 20_000)
        assertNotConfirmed(v)
        println("§17-2 关系/地面过期 → decision=${later.decision} 语音=${v.reason} text=\"${v.text}\"")
    }

    /**
     * 今晚 Demo 跑出来的第二条：关系已经判成"隔壁"，同一帧里 VLM 还报了门类型，
     * 于是门类型那句"那里是一个敞开的门洞"把"证据有冲突，请重新扫一下招牌和门"顶掉了。
     * 用户听到前者会以为系统在领他进门——冲突必须排在门类型前面。
     */
    @Test
    fun relationConflictOutranksDoorTypeHint() {
        val machine = Last10mStateMachine(TargetPoiProfile.build("瑞幸咖啡"), sessionId = "S1")
        machine.setInRange(true, 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        machine.onDoorDetected(10, 100, 90, 200, 3, 1_300, 1_300)
        machine.onVlmFacts(facts(RelationOutcome.CONFLICT, 4, 1_400), 1_400)
        val r = machine.evaluate(1_500, emptyList())
        assertEquals("前提：这一帧的结论是关系冲突", ArrivalDecision.RELATION_CONFLICT, r.decision)
        val sig = Last10mSpeechCue.signal(r, "S1", DoorType.OPEN_DOORWAY, ButtonLocation.ON_DOOR, null)
        assertEquals("冲突不能被门类型提示顶掉", SpeechCue.CONFLICT, sig.cue)
        assertTrue("要念的是重看关系：\"${sig.text}\"", sig.text.contains("冲突") || sig.text.contains("重新扫"))
        println("SPEECH 冲突优先于门类型 → cue=${sig.cue} text=\"${sig.text}\"")
    }

    // ---------------------------------------------------------------- 事件出口
    @Test
    fun everyDeclaredEventNameActuallyFires() {
        val seen = LinkedHashSet<String>()
        val machine = Last10mStateMachine(TargetPoiProfile.build("瑞幸咖啡"), sessionId = "S1")
        machine.events = { line -> seen.add(line.substringAfter("session=S1 ").split(" ").first()) }
        machine.setInRange(true, 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 1, 1_000), 1_000)
        machine.onTextHit(hit("瑞幸咖啡", 2, 1_200), 1_200)
        machine.onDebugDoor(DebugDoorPosition.LEFT, 1000, 1000, 3, 1_300, 1_300)
        machine.onDebugDoor(DebugDoorPosition.RIGHT, 1000, 1000, 4, 1_400, 1_400)
        machine.onVlmFacts(facts(RelationOutcome.LIKELY_SAME, 5, 1_500), 1_500)
        machine.onGroundEvidence(GroundEvidence(true, false, false, false, 6, 1_600, "人工声明",
            GroundSource.DEBUG_MANUAL, "S1"), null, 1_600)
        machine.evaluate(30_000, emptyList())            // 过期 → TARGET_EXPIRED / RELATION_INVALIDATED
        val core = setOf("TARGET_SEEN", "TARGET_LOCKED", "DOOR_ADDED", "MULTIPLE_DOORS",
            "RELATION_CONFIRMED", "GROUND_EVIDENCE", "STATE_TRANSITION", "DECISION", "NEED_VIEW",
            "TARGET_EXPIRED", "RELATION_INVALIDATED")
        val missing = core - seen
        println("EVENTS 触发=${seen.sorted()} 缺=$missing")
        assertTrue("这些事件必须真的发得出来：缺 $missing", missing.isEmpty())
        // 事件名清单里剩下的都由 Android 侧负责，这里核对它们都在名单上（防止改名漂移）
        val unknown = seen.filter { it !in Last10mEvents.NAMES }
        assertTrue("发出的事件名必须都在 Last10mEvents.NAMES 里：$unknown", unknown.isEmpty())
    }

    // ---------------------------------------------------------------- helpers
    private fun hit(text: String, frame: Long, ts: Long) =
        TextHit(text, 10, 10, 120, 40, 0.9, frame, ts, EvidenceSource.OCR)

    private fun facts(relation: RelationOutcome, frame: Long, ts: Long) = VlmFacts.fromFields(
        mapOf("target_visible" to true, "target_identity" to "瑞幸咖啡",
            "candidate_entrance_visible" to true,
            "entrance_relation" to when (relation) {
                RelationOutcome.LIKELY_SAME -> "same_storefront"
                RelationOutcome.CONFLICT -> "neighbor"
                RelationOutcome.UNKNOWN -> "unknown"
            }), frame, ts)

    private fun assertNotConfirmed(v: SpeechVerdict) {
        if (v is SpeechVerdict.Speak) {
            val bad = v.cue == SpeechCue.ARRIVAL_CONFIRMED ||
                v.text.contains("入口已确认") || v.text.contains("已找到入口") || v.text.contains("安全")
            assertFalse("污染序列里不能播出「${v.text}」(cue=${v.cue})", bad)
        }
    }
}
