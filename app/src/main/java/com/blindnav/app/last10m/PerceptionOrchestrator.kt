package com.blindnav.app.last10m

/**
 * 感知编排器：把"什么时候问、问谁、问不到怎么办"集中到一处（任务书第 5/6/7/19 节）。
 *
 * 三条职责：
 *
 *  1. **频率与触发**。每帧都跑云端 VLM 是不现实的（1.5–2s 一次，且画面早就换了），
 *     所以本地 OCR 每帧走，云端只在"缺信息"时低频走一次：
 *     目标已锁但还没关系 / 有多个候选门分不清 / needView 要求看门或看地面 / 用户主动问。
 *  2. **降级链**。按 `AUTO → VLM → REPLAY → DEBUG_MANUAL` 的顺序试，
 *     每一跳都记录原因（超时 / 无 key / 解析失败 / 该源没给结果）。
 *     这条链是今晚演示的命脉：网络挂了演示不能跟着挂。
 *  3. **来源诚实**。最终用的是哪一源，必须写进 [trail] 与面板，
 *     绝不能让人把"回放里的门"听成"实时检测到的门"。
 *
 * 这一层不做任何到达或运动判断，也不产生 TTS 文本；
 * 它只把视觉结果登记进状态机，然后由状态机决定说什么。
 */
class PerceptionOrchestrator(
    private val machine: Last10mStateMachine,
    private val profile: TargetPoiProfile,
    private val relationEngine: TargetDoorRelationEngine = TargetDoorRelationEngine(),
    /** 按优先级排列；生产默认 [云端 VLM, 回放, 人工]。 */
    private val reasoners: List<VisionReasoner>,
    private val clock: () -> Long,
    private val minIntervalMs: Long = 2_500L,
    private val events: (String) -> Unit = {},
    /** 帧尺寸由调用方给（来自真实帧，不是 View）。 */
    private val frameSize: () -> Pair<Int, Int> = { 0 to 0 }
) {

    data class Trail(val source: String, val outcome: String)

    data class Outcome(
        val applied: Boolean,
        val source: EvidenceSource?,
        val result: Last10mVisionResult?,
        val decisions: List<TargetDoorRelationEngine.Decision>,
        val trail: List<Trail>,
        val reason: String
    )

    private var lastAttemptAt = Long.MIN_VALUE / 2
    private var lastSceneAnswerAt = Long.MIN_VALUE / 2
    private val trail = ArrayList<Trail>()
    private var recentTexts = listOf<String>()
    /** 本地 OCR 最近一次的命中（带像素框）。关系判定要靠它算"招牌压在哪扇门上"。 */
    private var lastLocalHits: List<TextHit> = emptyList()

    var lastOutcome: Outcome? = null; private set
    var attempts = 0; private set
    var skipped = 0; private set

    /** 本地 OCR 每帧都吃：身份证据的积累不能等云端。 */
    fun onLocalTexts(hits: List<TextHit>, frameId: Long, timestampMs: Long, nowMs: Long) {
        recentTexts = hits.map { it.text }
        lastLocalHits = hits.take(12)
        for (h in hits) machine.onTextHit(h, nowMs)
        machine.evaluate(nowMs, hits.map { it.text })
    }

    /**
     * 是否该问一次云端。返回 false 时一次网络都不发（省时间也省电）。
     * 用户主动问"前面有什么"走 [forceAnalyze]，不受这里限制。
     */
    fun shouldAskNow(nowMs: Long): Boolean {
        if (reasoners.isEmpty()) return false
        if (nowMs - lastAttemptAt < minIntervalMs) return false
        val r = machine.evaluate(nowMs, recentTexts)
        // 还在 APPROACH / 已经确认到达：没必要问
        if (r.state == Last10mState.APPROACH || r.decision == ArrivalDecision.ARRIVAL_CONFIRMED) return false
        return when {
            !r.targetLocked -> false                       // 目标都没认出来，先扫招牌
            r.candidateCount == 0 -> true                   // 需要门
            r.reasonCodes.contains(ReasonCode.RELATION_UNKNOWN) -> true
            r.reasonCodes.contains(ReasonCode.MULTIPLE_DOORS) -> true
            r.reasonCodes.contains(ReasonCode.NEIGHBOR_CONFLICT) -> true
            r.groundState == GroundState.NOT_OBSERVED || r.groundState == GroundState.UNKNOWN -> true
            r.reasonCodes.contains(ReasonCode.GROUND_STALE) -> true
            else -> false
        }
    }

    /** 正常链路：满足触发条件才发请求。 */
    fun tick(jpeg: ByteArray?, frameId: Long, timestampMs: Long, nowMs: Long): Outcome? {
        if (!shouldAskNow(nowMs)) {
            skipped++
            return null
        }
        return forceAnalyze(jpeg, frameId, timestampMs, nowMs, trigger = "AUTO")
    }

    /** 一次"问"的结果，还没登记进状态机。 */
    data class Captured(
        val source: EvidenceSource,
        val result: Last10mVisionResult,
        val trail: List<Trail>,
        val frameWidth: Int,
        val frameHeight: Int
    )

    /** 用户主动问 / Demo 强制走一次：跳过触发条件，但仍然守最小间隔。 */
    fun forceAnalyze(jpeg: ByteArray?, frameId: Long, timestampMs: Long, nowMs: Long,
                     trigger: String, sceneQuestion: Boolean = false): Outcome =
        capture(jpeg, frameId, timestampMs, nowMs, trigger, sceneQuestion)?.let { deliver(it, nowMs) }
            ?: Outcome(false, null, null, emptyList(), lastTrail(), "NO_SOURCE_ANSWERED")

    /**
     * 只"问"，不动状态机。
     *
     * 为什么要拆：网络回来的那一刻，画面早就换了几帧。
     * 如果 ask 和 apply 绑在一起，就无法在中间插一句"这份结果还赶得上这一帧吗"。
     * 拆开后调用方可以先核对世代号，过期结果直接丢掉（同 OCR 的迟到结果处理）。
     */
    fun capture(jpeg: ByteArray?, frameId: Long, timestampMs: Long, nowMs: Long,
                trigger: String, sceneQuestion: Boolean = false): Captured? {
        attempts++
        lastAttemptAt = nowMs
        val (w, h) = frameSize()
        val context = VisionContext(
            targetName = profile.poiName,
            targetAliases = profile.strongAliases,
            recentTexts = recentTexts,
            targetLocked = machine.targetSnapshot().status == TargetLock.Status.TARGET_LOCKED,
            knownDoorBoxes = machine.candidates().mapNotNull { c ->
                val box = permille(c, w, h) ?: return@mapNotNull null
                VisionDoor(c.doorId, box, null)
            },
            wantsSceneDescription = sceneQuestion,
            focusOnActiveDoor = machine.activeDoorId() != null,
            frameWidth = w, frameHeight = h)
        val used = ArrayList<Trail>()
        for (reasoner in reasoners) {
            val label = reasoner.source.label()
            var result: Last10mVisionResult? = null
            var failed = false
            try {
                result = reasoner.analyze(jpeg, frameId, timestampMs, profile, context)
            } catch (e: Throwable) {
                failed = true
                used.add(Trail(label, "THREW=${e.javaClass.simpleName}"))
                events("VISION_SOURCE_FAILED src=$label err=${e.javaClass.simpleName}")
            }
            if (failed) continue
            if (result == null) {
                used.add(Trail(label, "NO_RESULT"))
                events("VISION_SOURCE_SKIP src=$label reason=no_result")
                continue
            }
            if (!result.parseOk) {
                used.add(Trail(label, "UNPARSEABLE=${result.violations}"))
                events("VISION_SOURCE_SKIP src=$label reason=${result.violations}")
                // 结果不可信：整份丢掉，但仍然算"这一源答过了"，降级到下一源
                continue
            }
            used.add(Trail(label, "ANSWERED"))
            trail.clear(); trail.addAll(used)
            events("VISION_ANSWERED src=$label trigger=$trigger doors=${result.doors.size} " +
                "texts=${result.texts.size} latency=${result.latencyMs}ms chain=${used.map { it.source + ":" + it.outcome }}")
            return Captured(result.source, result, used.toList(), w, h)
        }
        trail.clear(); trail.addAll(used)
        events("VISION_ALL_FAILED trigger=$trigger chain=${used.map { "${it.source}:${it.outcome}" }}")
        return null
    }

    /** 把一次已经确认"还赶得上"的结果登记进状态机并做关系判定。 */
    fun deliver(captured: Captured, nowMs: Long): Outcome {
        val result = captured.result
        val applied = machine.onVisionResult(result, captured.frameWidth, captured.frameHeight, nowMs)
        // 关系判定的文字必须与候选门在同一坐标空间：状态机已把视觉文字换算成像素，
        // 这里取换算后的那份，不能再拿 result.texts 里的千分比去和像素门框比重叠。
        val decisions = relationEngine.decide(
            profile = profile,
            doors = machine.candidates(),
            texts = mergedTexts(machine.visionTextHits()),
            visionRelations = result.relations,
            doorIdToVisionId = machine.visionDoorMapping(),
            addressSupport = if (machine.addressOutcome() == AddressOutcome.MATCH) true
                else if (machine.addressOutcome() == AddressOutcome.CONFLICT) false else null,
            landmarkConflict = machine.landmarkConflict())
        machine.applyRelationDecisions(decisions, result.source, nowMs)
        val outcome = Outcome(true, result.source, result, decisions, captured.trail, "")
        lastOutcome = outcome
        events("VISION_APPLIED_SRC src=${result.source.label()} texts=${applied.texts} " +
            "doors=${applied.doors} new=${applied.newDoors} ground=${applied.groundAttached} " +
            "verdicts=${decisions.map { "${it.doorId}:${it.verdict.name}" }}")
        return outcome
    }

    /** 用户问"前面有什么"：只取一份当前帧的场景描述。 */
    fun sceneQuery(jpeg: ByteArray?, frameId: Long, timestampMs: Long, nowMs: Long): Outcome? {
        if (nowMs - lastSceneAnswerAt < SCENE_QUERY_MIN_MS) return null
        lastSceneAnswerAt = nowMs
        return forceAnalyze(jpeg, frameId, timestampMs, nowMs, trigger = "SCENE_QUERY", sceneQuestion = true)
    }

    fun chain(): List<String> = reasoners.map { it.source.label() }
    fun lastTrail(): List<Trail> = trail.toList()

    /**
     * 关系判定用的文字 = 这一份视觉结果里的文字 + 本地 OCR 最近几帧的命中。
     * 两路按"文本 + 框"去重，同一句话被两个源各报一次时不能算成两条独立依据。
     */
    private fun mergedTexts(vision: List<TextHit>): List<TextHit> {
        val out = ArrayList<TextHit>()
        val seen = HashSet<String>()
        for (t in vision + lastLocalHits) {
            val key = t.text.trim() + "@" + t.left + "," + t.top + "," + t.right + "," + t.bottom
            if (seen.add(key)) out.add(t)
        }
        return out
    }

    private fun permille(c: EntranceCandidate, w: Int, h: Int): IntArray? {
        if (w <= 0 || h <= 0) return null
        return intArrayOf(
            (c.left * 1000 / w).coerceIn(0, 1000), (c.top * 1000 / h).coerceIn(0, 1000),
            (c.right * 1000 / w).coerceIn(0, 1000), (c.bottom * 1000 / h).coerceIn(0, 1000))
    }

    companion object {
        /** 主动询问的防连点间隔：一句话没说完再点一次，只会更听不清。 */
        const val SCENE_QUERY_MIN_MS = 4_000L
    }
}
