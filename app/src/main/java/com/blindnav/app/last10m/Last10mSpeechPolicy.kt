package com.blindnav.app.last10m

/**
 * 最后十米的播报裁决（任务书第 3 节 / PF-02）。
 *
 * 为什么必须单独一层：会话里每帧都会 evaluate 一次（OCR 3 帧/秒，还有手动点门），
 * 如果每次拿到 prompt 就念，用户站在门口听到的是每秒三遍同一句话。
 * 而直接 `speakPrompts = false` 又走到另一个极端——视障用户什么都听不到。
 *
 * 优先级（声明顺序就是优先级）：
 *   STOP        P0 安全停（障碍/横穿/请停下）——最后十米**永远不许打断它**
 *   LAST10M     P1 门口关键操作提示
 *   NAVIGATION  P2 普通导航提示
 *   SCENE       P3 环境描述
 *
 * 四条硬规则：
 *  1. 只有 (state / decision / needView / 语义 cue / 关系冲突 / 目标锁变 / 到达) 发生变化才产生新语音；
 *  2. 同一语义默认冷却 2000ms，`TARGET_LOCKED` 不连续重复，`TARGET_LOST` 只在 locked→lost 那一瞬播一次；
 *  3. `ARRIVAL_CONFIRMED` 每个会话最多一次，并且可以压掉低优先级的环境描述；
 *  4. 措辞在出口再拦一次：不许出现"安全/可以通过/放心/往左走/多少米"。
 *     人工声明的地面只能说"门前区域已记录"，不能说"门前安全"。
 *
 * 这一层是纯 Kotlin：时钟与外部高优先级事件都由调用方注入，所以整套裁决能在电脑上测。
 */
enum class Last10mPriority { STOP, LAST10M, NAVIGATION, SCENE }

/** 播报的语义身份。去重按这个走，不按字符串——同义改写法不该被当成"新消息"。 */
enum class SpeechCue {
    NONE,
    KEEP_SEARCHING,       // 还在找目标招牌
    TARGET_LOCKED,        // 认出目标了（只播一次）
    LOOK_AT_DOOR,         // 顺着招牌下方找入口
    MULTIPLE_DOORS,       // 有多个入口，先别进去
    RELATION_UNKNOWN,     // 还不能确认这扇是不是目标
    CONFLICT,            // 证据互相矛盾，重扫
    LOOK_DOWN_GROUND,     // 请拍一下门前
    GROUND_RECORDED,      // 门前区域已记录（人工声明）
    GROUND_HAZARD,        // 门前看到台阶/路缘/阻挡（临时结论，说"可能"）
    DOOR_TYPE,            // 门类型（看到了才说，UNKNOWN 不说）
    DOOR_BUTTON,          // 开门按钮位置（同上）
    SCENE_ANSWER,         // 用户主动问"前面有什么"的回答
    TARGET_LOST,          // 目标招牌离开画面
    ARRIVAL_CONFIRMED     // 入口已确认
}

/** 一次裁决的输入：状态机产出的全部相关信号，加上会话身份。 */
data class Last10mSignal(
    val state: Last10mState,
    val decision: ArrivalDecision,
    val needView: NeedView,
    val cue: SpeechCue,
    val text: String,
    val targetLocked: Boolean,
    val relationConflict: Boolean,
    val neighborConflict: Boolean,
    val groundState: GroundState,
    val candidateCount: Int,
    val sessionId: String? = null,
    val priority: Last10mPriority = Last10mPriority.LAST10M
)

sealed class SpeechVerdict {
    abstract val cue: SpeechCue
    abstract val text: String
    /** 日志与面板都要能回答"这条为什么没说"。 */
    abstract val reason: String

    data class Speak(override val cue: SpeechCue, override val text: String,
                     override val reason: String) : SpeechVerdict() {
        val speaks: Boolean get() = true
    }

    data class Suppress(override val cue: SpeechCue, override val text: String,
                        override val reason: String) : SpeechVerdict() {
        val speaks: Boolean get() = false
    }
}

class Last10mSpeechPolicy(
    private val cooldownMs: Long = DEFAULT_COOLDOWN_MS,
    /** 到达之后压住环境描述的时长：这句是这一段路的结论，不该被"左边有根柱子"盖掉。 */
    private val sceneMuteAfterArrivalMs: Long = 8_000L,
    /** 一次 STOP 之后多久内不许被最后十米打断。 */
    private val stopHoldMs: Long = 3_000L
) {

    companion object {
        const val DEFAULT_COOLDOWN_MS = 2_000L
        /** 不能用 Long.MIN_VALUE 当"从未发生"哨兵：减法会溢出（scene/SpeechPolicy 踩过）。 */
        private const val NOT_SET = -1L

        /** 每个会话只说一次的语义：说了第二次只会盖掉别的提示。 */
        val ONCE_PER_SESSION = setOf(
            SpeechCue.ARRIVAL_CONFIRMED, SpeechCue.DOOR_TYPE, SpeechCue.DOOR_BUTTON)

        /** 出口禁用词：移动指令、距离承诺、安全承诺。 */
        private val FORBIDDEN = Regex(
            "安全|可以通过|可以进|放心|[0-9]+\\s*[米步]|往[左右]边?走|向[左右]边?挪|跟我走|往前走")
    }

    private var lastSeen: Last10mSignal? = null
    private var lastSpokenCue: SpeechCue = SpeechCue.NONE
    private var lastSpokenAt = NOT_SET
    private var lastSpokenText = ""
    private val lastSpokenAtByCue = HashMap<SpeechCue, Long>()
    private var wasTargetLocked = false
    private var stopActiveUntil = NOT_SET
    private var sceneMutedUntil = NOT_SET
    /** "每会话只说一次"的语义提示，跟着会话 id 走，换目标后可以重新说。 */
    private data class SignalKey(val cue: SpeechCue, val session: String?)
    private val announcedOnce = HashSet<SignalKey>()

    var spoken = 0L; private set
    var suppressed = 0L; private set
    var lastDecisionReason = "init"
        private set

    /** STOP/障碍这一类更高优先级的播报发生时通知本策略，P1 期间不再插嘴。 */
    fun noteHigherPriority(nowMs: Long, holdMs: Long = stopHoldMs) {
        stopActiveUntil = nowMs + holdMs
    }

    /** 环境描述（P3）现在能不能播。 */
    fun sceneAllowed(nowMs: Long): Boolean = nowMs >= sceneMutedUntil

    /** 高优先级能不能被低优先级打断：永远不行。 */
    fun canPreempt(from: Last10mPriority, to: Last10mPriority): Boolean = to.ordinal <= from.ordinal

    fun decide(signal: Last10mSignal, nowMs: Long): SpeechVerdict {
        val previous = lastSeen
        lastSeen = signal

        if (signal.cue == SpeechCue.NONE || signal.text.isBlank()) {
            return suppress(SpeechVerdict.Suppress(signal.cue, signal.text, "no_content"))
        }
        val hit = FORBIDDEN.find(signal.text)
        if (hit != null) {
            return suppress(SpeechVerdict.Suppress(
                signal.cue, signal.text, "forbidden_phrasing=${hit.value}"))
        }
        if (signal.priority == Last10mPriority.SCENE) {
            return suppress(SpeechVerdict.Suppress(signal.cue, signal.text, "scene_priority_cannot_be_last10m_voice"))
        }
        if (nowMs < stopActiveUntil) {
            return suppress(SpeechVerdict.Suppress(signal.cue, signal.text, "stop_has_priority"))
        }
        if (ONCE_PER_SESSION.contains(signal.cue) &&
            announcedOnce.contains(SignalKey(signal.cue, signal.sessionId))) {
            return suppress(SpeechVerdict.Suppress(
                signal.cue, signal.text, "${signal.cue.name}_already_announced_this_session"))
        }
        if (signal.cue == SpeechCue.TARGET_LOST && !wasTargetLocked) {
            // 只有 locked → lost 那一次跳变才播。之后每帧都是"还没找回来"，重复念只会盖掉别的提示。
            return suppress(SpeechVerdict.Suppress(signal.cue, signal.text, "target_lost_not_a_transition"))
        }
        if (signal.cue == SpeechCue.TARGET_LOCKED && lastSpokenCue == SpeechCue.TARGET_LOCKED) {
            return suppress(SpeechVerdict.Suppress(signal.cue, signal.text, "already_announced_locked"))
        }
        val unchanged = previous != null &&
            previous.state == signal.state &&
            previous.decision == signal.decision &&
            previous.needView == signal.needView &&
            previous.cue == signal.cue &&
            previous.relationConflict == signal.relationConflict &&
            previous.neighborConflict == signal.neighborConflict &&
            previous.groundState == signal.groundState &&
            previous.candidateCount == signal.candidateCount
        if (unchanged) {
            return suppress(SpeechVerdict.Suppress(signal.cue, signal.text, "nothing_changed"))
        }
        if (signal.cue == lastSpokenCue && lastSpokenAt != NOT_SET &&
            nowMs - lastSpokenAt < cooldownMs) {
            return suppress(SpeechVerdict.Suppress(
                signal.cue, signal.text, "cooldown=${nowMs - lastSpokenAt}<${cooldownMs}ms"))
        }
        // 冷却按语义分别记：A→B→A 这种来回（多门→看地面→又多门）里，
        // 第二次 A 距离第一次 A 往往不到两秒，只看"上一条"会把它当成新消息念出去。
        val lastAtThisCue = lastSpokenAtByCue[signal.cue]
        if (lastAtThisCue != null && nowMs - lastAtThisCue < cooldownMs &&
            !ONCE_PER_SESSION.contains(signal.cue)) {
            return suppress(SpeechVerdict.Suppress(
                signal.cue, signal.text, "cue_cooldown=${nowMs - lastAtThisCue}<${cooldownMs}ms"))
        }

        spoken++
        lastSpokenCue = signal.cue
        lastSpokenAt = nowMs
        lastSpokenText = signal.text
        lastSpokenAtByCue[signal.cue] = nowMs
        wasTargetLocked = signal.targetLocked
        if (signal.cue == SpeechCue.ARRIVAL_CONFIRMED) {
            sceneMutedUntil = nowMs + sceneMuteAfterArrivalMs
        }
        if (ONCE_PER_SESSION.contains(signal.cue)) announcedOnce.add(SignalKey(signal.cue, signal.sessionId))
        if (signal.cue == SpeechCue.TARGET_LOST) wasTargetLocked = false
        lastDecisionReason = "speak:${signal.cue.name}"
        return SpeechVerdict.Speak(signal.cue, signal.text, "changed")
    }

    private fun suppress(v: SpeechVerdict.Suppress): SpeechVerdict {
        suppressed++
        lastDecisionReason = "suppress:${v.reason}"
        return v
    }

    /** 面板用：最后一次说了什么、为什么这条没说。 */
    fun lastSpoken(): String = if (lastSpokenAt == NOT_SET) "-" else "$lastSpokenText"
    fun lastSpokenAt(): Long = lastSpokenAt
    fun lastCue(): SpeechCue = lastSpokenCue

    /**
     * 换目标 / 重开一版采集时清历史，但不能假装"从没到过"——
     * ARRIVAL 的"每会话一次"跟着 sessionId 走，reset 只清冷却与上一帧信号。
     */
    fun reset() {
        lastSeen = null
        lastSpokenCue = SpeechCue.NONE
        lastSpokenAt = NOT_SET
        lastSpokenText = ""
        lastSpokenAtByCue.clear()
        wasTargetLocked = false
        stopActiveUntil = NOT_SET
        sceneMutedUntil = NOT_SET
    }

    /** 换目标 / 重开一段路：连"说过一次"的记录一起清掉。 */
    fun resetAll() {
        reset()
        announcedOnce.clear()
    }

    /**
     * 直接播一条，不进去重逻辑。
     * 只用于**用户主动提问的回答**：他问了就必须答，
     * 这时候用"上一句说过类似的了"来压掉回答是不可接受的。
     * STOP 仍然优先——安全提示不能被一句环境描述盖掉。
     */
    fun answerNow(text: String, nowMs: Long, stopActive: Boolean): SpeechVerdict {
        if (text.isBlank()) return SpeechVerdict.Suppress(SpeechCue.SCENE_ANSWER, text, "empty_answer")
        val hit = FORBIDDEN.find(text)
        if (hit != null) return SpeechVerdict.Suppress(
            SpeechCue.SCENE_ANSWER, text, "forbidden_phrasing=${hit.value}")
        if (stopActive) return SpeechVerdict.Suppress(
            SpeechCue.SCENE_ANSWER, text, "stop_has_priority")
        spoken++
        lastSpokenCue = SpeechCue.SCENE_ANSWER
        lastSpokenAt = nowMs
        lastSpokenText = text
        lastSpokenAtByCue[SpeechCue.SCENE_ANSWER] = nowMs
        lastDecisionReason = "answer:direct"
        return SpeechVerdict.Speak(SpeechCue.SCENE_ANSWER, text, "user_asked")
    }
}

/**
 * 状态机结果 → 语义 cue。这里刻意**不**用 prompt 文本当键：
 * 同一句提示改个字就会绕过去重，那是自欺。
 */
object Last10mSpeechCue {

    fun of(r: Last10mStateMachine.Result, targetJustLost: Boolean = false): SpeechCue = when {
        r.decision == ArrivalDecision.ARRIVAL_CONFIRMED -> SpeechCue.ARRIVAL_CONFIRMED
        r.decision == ArrivalDecision.RELATION_CONFLICT -> SpeechCue.CONFLICT
        r.decision == ArrivalDecision.TARGET_LOST -> SpeechCue.TARGET_LOST
        r.reasonCodes.contains(ReasonCode.SESSION_MISMATCH) -> SpeechCue.CONFLICT
        r.reasonCodes.contains(ReasonCode.NEIGHBOR_CONFLICT) &&
            r.reasonCodes.contains(ReasonCode.MULTIPLE_DOORS) -> SpeechCue.MULTIPLE_DOORS
        r.reasonCodes.contains(ReasonCode.MULTIPLE_DOORS) -> SpeechCue.MULTIPLE_DOORS
        r.decision == ArrivalDecision.LOOK_DOWN_GROUND -> SpeechCue.LOOK_DOWN_GROUND
        r.groundState == GroundState.OBSERVED_MANUAL || r.groundState == GroundState.CLEAR ->
            if (r.decision == ArrivalDecision.KEEP_SEARCHING &&
                r.state == Last10mState.VERIFY_RELATION) SpeechCue.RELATION_UNKNOWN else SpeechCue.GROUND_RECORDED
        r.state == Last10mState.TARGET_LOCKED -> SpeechCue.TARGET_LOCKED
        r.state == Last10mState.SEARCH_TARGET ->
            if (targetJustLost) SpeechCue.TARGET_LOST else SpeechCue.KEEP_SEARCHING
        r.state == Last10mState.SEARCH_ENTRANCE || r.state == Last10mState.ENTRANCE_CANDIDATE ->
            SpeechCue.LOOK_AT_DOOR
        r.state == Last10mState.VERIFY_RELATION -> SpeechCue.RELATION_UNKNOWN
        r.state == Last10mState.VERIFY_GROUND -> SpeechCue.LOOK_DOWN_GROUND
        r.state == Last10mState.REOBSERVE -> SpeechCue.CONFLICT
        else -> SpeechCue.NONE
    }

    /**
     * 要念的文本。默认用"下一步看哪里"的话术（它比 decision 话术更具体），
     * 但 GROUND_RECORDED 要单独给一句如实回执：人工声明的地面只能说"已记录"，
     * 不能借 ARRIVAL 的话术被听成"门前安全"。
     */
    fun textFor(r: Last10mStateMachine.Result): String = when (of(r)) {
        SpeechCue.GROUND_RECORDED -> Last10mPrompts.GROUND_RECORDED
        else -> r.viewPrompt.ifBlank { r.prompt }
    }

    fun signal(r: Last10mStateMachine.Result, sessionId: String?, targetJustLost: Boolean = false): Last10mSignal =
        signal(r, sessionId, null, null, null, targetJustLost)

    /**
     * 带门类型 / 按钮 / 地面风险的版本（任务书第 10/11/15 节）。
     * 这三类信息只在"刚知道"的那一刻插播一次——它们比"继续扫描"更值得被听到，
     * 但反复念就成了噪音。
     */
    fun signal(
        r: Last10mStateMachine.Result, sessionId: String?,
        doorType: DoorType?, button: ButtonLocation?, groundHazard: String?,
        targetJustLost: Boolean = false
    ): Last10mSignal {
        val base = of(r, targetJustLost)
        // 冲突（"这扇可能不是它家门"）比"门是什么类型"更值得先说：
        // 用户听到"那里是一个敞开的门洞"会以为系统在带他进去，
        // 而当下真正该做的动作是重看招牌与门的关系。
        val infoAllowed = base != SpeechCue.CONFLICT && base != SpeechCue.ARRIVAL_CONFIRMED
        val (cue, text) = when {
            infoAllowed && doorType != null && doorType != DoorType.UNKNOWN &&
                r.needView != NeedView.NONE -> SpeechCue.DOOR_TYPE to (Last10mPrompts.forDoorType(doorType) ?: "")
            infoAllowed && button != null && button != ButtonLocation.UNKNOWN &&
                r.needView != NeedView.NONE -> SpeechCue.DOOR_BUTTON to (Last10mPrompts.forButton(button) ?: "")
            groundHazard != null && r.decision != ArrivalDecision.ARRIVAL_CONFIRMED ->
                SpeechCue.GROUND_HAZARD to groundHazard
            else -> base to textFor(r)
        }
        return Last10mSignal(
            state = r.state, decision = r.decision, needView = r.needView,
            cue = if (text.isBlank()) base else cue,
            text = if (text.isBlank()) textFor(r) else text,
            targetLocked = r.targetLocked,
            relationConflict = r.reasonCodes.contains(ReasonCode.RELATION_CONFLICT),
            neighborConflict = r.reasonCodes.contains(ReasonCode.NEIGHBOR_CONFLICT),
            groundState = r.groundState,
            candidateCount = r.candidateCount,
            sessionId = sessionId)
    }
}
