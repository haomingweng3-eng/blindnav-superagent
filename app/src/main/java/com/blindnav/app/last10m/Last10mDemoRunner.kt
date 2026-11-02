package com.blindnav.app.last10m

/**
 * Demo 回放的驱动器（任务书第 16/17 节）。纯 Kotlin，能在电脑上把整场演示跑一遍并断言。
 *
 * 一个原则：**演示不许有专用实现**。回放走的必须是生产同一套
 * [PerceptionOrchestrator] → [Last10mStateMachine] → [ArrivalAuthority] → [Last10mSpeechPolicy]，
 * 只把视觉源换成 [ReplayVisionReasoner]。否则明天演的是这个 runner，真机上跑的是另一套代码，
 * 演示通过与否说明不了产品状态。
 *
 * 每一帧产出的 [FrameOutcome] 同时给人看三样东西：
 *  - 系统认为现在在哪一步（人话）；
 *  - 下一步要用户做什么（needView 话术）；
 *  - 这个结论是**谁**给的（来源链：REPLAY / DEBUG_MANUAL / REMOTE_VLM）。
 */
/**
 * 演示里两帧之间的间隔（毫秒）。取 2 秒是按『人挪一步再看一眼』的节奏：
 * 比候选门的归并窗口（1.5s）长，所以上一帧没再看到的门会自然退出，
 * 不会攒成假的『有多个入口』冲突。
 */
private const val FRAME_INTERVAL_MS = 2_000L

class Last10mDemoRunner(
    val scenario: DemoScenario,
    private val clock: () -> Long = { 0L },
    /** 演示里也允许人补一条地面声明（第 3 个场景的台阶就是这种情况），来源照样标 DEBUG_MANUAL。 */
    private val allowManualGround: Boolean = true
) {
    val profile: TargetPoiProfile = TargetPoiProfile.build(
        scenario.targetName, scenario.targetAliases)
    val machine = Last10mStateMachine(profile, sessionId = "demo-${scenario.id}")
    private val reasoner = ReplayVisionReasoner(scenario.frames.map {
        ReplayVisionReasoner.ReplayFrame(it.label, it.json)
    }, loop = false)
    private val orchestrator = PerceptionOrchestrator(
        machine = machine, profile = profile, reasoners = listOf(reasoner),
        clock = clock, minIntervalMs = 0L, frameSize = { frameWidth to frameHeight })
    private val speech = Last10mSpeechPolicy()

    /** 回放图的实际尺寸由 Activity 解码后注入；没有就先给一个安全默认值。 */
    var frameWidth: Int = 768
    var frameHeight: Int = 1024

    private var frameIndex = -1
    private var lastOutcome: FrameOutcome? = null
    private var timeline = 1_000L

    val frameCount: Int get() = scenario.frames.size
    val currentIndex: Int get() = frameIndex
    val currentFrame: DemoFrame? get() = scenario.frames.getOrNull(frameIndex)
    val finished: Boolean get() = frameIndex >= scenario.frames.size - 1

    data class FrameOutcome(
        val index: Int,
        val label: String,
        val image: String,
        val note: String,
        val source: String,
        val trail: String,
        val state: Last10mState,
        val decision: ArrivalDecision,
        val needView: NeedView,
        val reasons: List<ReasonCode>,
        val doors: Int,
        val activeDoor: String?,
        val relationSummary: String,
        val ground: GroundState,
        val groundSource: GroundSource,
        val doorType: DoorType,
        val button: ButtonLocation,
        val sceneSummary: String?,
        val spoken: String,
        val speechWhy: String,
        val header: String
    )

    fun reset() {
        reasoner.goTo(0)
        frameIndex = -1
        timeline = 1_000L
        lastOutcome = null
        speech.resetAll()
    }

    /** 手动把地面声明挂到当前活跃门上（第 3 个场景用；来源永远是 DEBUG_MANUAL）。 */
    fun declareGroundManually(step: Boolean = false, curb: Boolean = false, obstruction: Boolean = false): Boolean {
        if (!allowManualGround) return false
        val now = timeline
        machine.onGroundEvidence(GroundEvidence(true, step, curb, obstruction,
            frameIdOf(now), now, "人工声明（Demo）", GroundSource.DEBUG_MANUAL, machine.sessionId()), null, now)
        return true
    }

    fun step(): FrameOutcome? {
        if (frameIndex + 1 >= scenario.frames.size) return null
        frameIndex++
        reasoner.goTo(frameIndex)
        val frame = scenario.frames[frameIndex]
        timeline += FRAME_INTERVAL_MS
        val now = timeline
        machine.setInRange(true, now)
        // 回放没有独立 OCR：文字证据同样来自那一帧的视觉结果，先 capture 再按来源喂进身份
        val captured = orchestrator.capture(null, frameIdOf(now), now, now, trigger = "DEMO")
            ?: return FrameOutcome(frameIndex, frame.label, frame.image, frame.note, "NONE", "-",
                machine.evaluate(now, emptyList()).state, ArrivalDecision.KEEP_SEARCHING,
                NeedView.NONE, listOf(ReasonCode.TARGET_NOT_FOUND), 0, null, "-",
                GroundState.NOT_OBSERVED, GroundSource.UNKNOWN, DoorType.UNKNOWN, ButtonLocation.UNKNOWN,
                null, "", "no_source", "回放帧没有被接受").also { lastOutcome = it }
        val outcome = orchestrator.deliver(captured, now)
        val result = captured.result
        // 文字要进身份链：回放的文本来源是 REPLAY，不能冒充本地 OCR
        for (t in result.texts) machine.onTextHit(t, now)
        val r = machine.evaluate(now, result.texts.map { it.text })
        val hazard = Last10mPrompts.forGroundHazard(
            result.ground.step == true, result.ground.curb == true, result.ground.obstruction == true)
        val signal = Last10mSpeechCue.signal(r, machine.sessionId(),
            if (r.candidateCount <= 1) result.doorType else null,
            if (r.candidateCount <= 1) result.buttonLocation else null,
            hazard, targetJustLost = false)
        val verdict = speech.decide(signal, now)
        val spoken = when (verdict) {
            is SpeechVerdict.Speak -> verdict.text
            is SpeechVerdict.Suppress -> ""
        }
        val outcomeLine = FrameOutcome(
            index = frameIndex, label = frame.label, image = frame.image, note = frame.note,
            source = captured.source.label(),
            trail = outcome.trail.joinToString(" → ") { "${it.source}:${it.outcome}" },
            state = r.state, decision = r.decision, needView = r.needView, reasons = r.reasonCodes,
            doors = r.candidateCount, activeDoor = r.activeDoorId,
            relationSummary = machine.candidates().joinToString("; ") {
                "${it.doorId}=${when (it.relationConfirmed) { true -> "目标"; false -> "隔壁"; null -> "未判" }}" +
                    "(${it.relationSource?.label() ?: "NONE"})"
            }.ifEmpty { "无候选" },
            ground = r.groundState, groundSource = r.groundSource,
            doorType = result.doorType, button = result.buttonLocation,
            sceneSummary = result.sceneSummary,
            spoken = spoken, speechWhy = verdict.reason,
            header = humanHeader(r, result.sceneSummary))
        lastOutcome = outcomeLine
        return outcomeLine
    }

    /** 倒回上一帧：重放而不是"撤销"——回放本身是确定性的，重算最可靠。 */
    fun back(): FrameOutcome? {
        val target = (frameIndex - 1).coerceAtLeast(-1)
        reset()
        var last: FrameOutcome? = null
        while (currentIndex < target) { last = step() }
        return last
    }

    fun last(): FrameOutcome? = lastOutcome

    private fun frameIdOf(now: Long): Long = 100L + now / 100L

    /** 顶部给人看的三行：目标 / 现在 / 下一步。老师不需要懂 reason code 也能明白系统卡在哪。 */
    private fun humanHeader(r: Last10mStateMachine.Result, scene: String?): String = buildString {
        append("目标：").append(profile.poiName).append('\n')
        append("当前：").append(NowSay.of(r.state, r.decision, r.candidateCount)).append('\n')
        append("下一步：").append(r.viewPrompt.ifBlank { Last10mPrompts.forDecision(r.decision, r.state) })
        if (!scene.isNullOrBlank()) append("\n画面：").append(scene)
    }
}

/** 回放场景的静态描述。assets 与测试资源用同一份格式，避免"测试绿了但包里没有"。 */
data class DemoScenario(
    val id: String,
    val title: String,
    val targetName: String,
    val targetAliases: List<String>,
    val provenance: String,
    val frames: List<DemoFrame>
)

data class DemoFrame(val label: String, val image: String, val json: String, val note: String)

/** `scenarios.json` 的解析（用生产同一个 [MiniJson]，所以这条路径也能在电脑上测）。 */
object DemoScenarios {
    fun parse(text: String): List<DemoScenario> {
        val root = MiniJson.parseObject(text) ?: return emptyList()
        return root.objList("scenarios").mapNotNull { s ->
            val id = s.string("id") ?: return@mapNotNull null
            val target = s.obj("target")
            val frames = s.objList("frames").mapNotNull { f ->
                val json = (f["json"] as? String) ?: return@mapNotNull null
                DemoFrame(f.string("label") ?: "frame", f.string("image") ?: "", json,
                    f.string("note") ?: "")
            }
            if (frames.isEmpty()) return@mapNotNull null
            DemoScenario(id, s.string("title") ?: id, target.string("name") ?: "目标",
                (target?.get("aliases") as? List<*>)?.mapNotNull { it as? String } ?: emptyList(),
                s.string("provenance") ?: "UNKNOWN", frames)
        }
    }
}

/**
 * "系统现在在哪一步"的人话版本。
 * 与 reason code 分开：reasonCode 是给工程师看的，这一份是给使用者与演示对象看的。
 */
object NowSay {
    fun of(state: Last10mState, decision: ArrivalDecision, doors: Int): String = when {
        state == Last10mState.APPROACH -> "还没有进入目的地附近"
        state == Last10mState.SEARCH_TARGET -> "在找目标招牌"
        state == Last10mState.TARGET_LOCKED -> "已找到目标，正在找入口"
        state == Last10mState.SEARCH_ENTRANCE -> "已找到目标，正在找入口"
        state == Last10mState.ENTRANCE_CANDIDATE -> "看到候选入口，正在确认是不是它家的门"
        state == Last10mState.VERIFY_RELATION && decision == ArrivalDecision.RELATION_CONFLICT ->
            "那扇门被判成别家的入口，正在重新确认"
        state == Last10mState.VERIFY_RELATION -> "正在确认这扇门是不是它家的"
        state == Last10mState.VERIFY_GROUND -> "入口已确认，正在看门前区域"
        state == Last10mState.ARRIVAL_CONFIRMED -> "入口已确认"
        state == Last10mState.REOBSERVE && doors >= 2 -> "发现多个可能入口，正在分清哪一扇"
        state == Last10mState.REOBSERVE -> "证据不够，需要重新观察"
        else -> "正在观察"
    }
}
