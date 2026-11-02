package com.blindnav.app

import android.content.Context
import android.graphics.Bitmap
import android.os.SystemClock
import android.util.Log
import com.blindnav.app.last10m.ArrivalDecision
import com.blindnav.app.last10m.ButtonLocation
import com.blindnav.app.last10m.DoorType
import com.blindnav.app.last10m.EvidenceSource
import com.blindnav.app.last10m.NeedViewPlanner
import com.blindnav.app.last10m.NowSay
import com.blindnav.app.last10m.PerceptionOrchestrator
import com.blindnav.app.last10m.ReplayVisionReasoner
import com.blindnav.app.last10m.TargetDoorRelationEngine
import com.blindnav.app.last10m.label
import com.blindnav.app.last10m.VisionReasoner
import com.blindnav.app.last10m.DebugDoorPosition
import com.blindnav.app.last10m.DebugDoorProposal
import com.blindnav.app.last10m.GroundEvidence
import com.blindnav.app.last10m.Last10mPrompts
import com.blindnav.app.last10m.OcrGate
import com.blindnav.app.last10m.GroundSource
import com.blindnav.app.last10m.GroundState
import com.blindnav.app.last10m.Last10mSessionStats
import com.blindnav.app.last10m.Last10mSpeechCue
import com.blindnav.app.last10m.Last10mSpeechPolicy
import com.blindnav.app.last10m.Last10mState
import com.blindnav.app.last10m.Last10mStateMachine
import com.blindnav.app.last10m.ScanDirection
import com.blindnav.app.last10m.SpeechCue
import com.blindnav.app.last10m.SpeechVerdict
import com.blindnav.app.last10m.TargetPoiProfile
import com.blindnav.app.last10m.TextHit
import com.blindnav.app.shadow.Last10mCaptureStore
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong

/**
 * 最后十米会话：把 CameraX 帧、OCR、VLM 事实、地面证据接到确定性状态机上。
 *
 * 四条刻意的设计边界：
 *  1. **不自动从招牌框造门**。"看到招牌 ≠ 找到入口"是这一功能的核心，
 *     如果系统拿招牌位置反推一个门框，就把最容易犯的错（隔壁门当目标入口）
 *     变成了实现默认。入口候选只能由显式检测/人工标注产生，并且必须写明来源。
 *  2. **播报经过 [Last10mSpeechPolicy]**。不是每次 evaluate 都念，
 *     也不许打断 STOP 级播报；人工声明的地面只说"已记录"，绝不说"安全"。
 *  3. **帧证据带 frameId、时间戳与会话身份**。TargetLock/EntranceTracker 的所有
 *     过期判断基于这两个数，跨会话证据在裁决层直接被拒。
 *  4. **每个关键事件都写 logcat**（`Last10mEvents`）。现场"点了没反应"必须能事后查。
 */
class Last10mSession(
    context: Context,
    val profile: TargetPoiProfile,
    /** 默认开声。上一版这里传 false，导致真机上最后十米对使用者完全沉默（PF-02）。 */
    private val speakPrompts: Boolean = true,
    private val addressExpectation: com.blindnav.app.last10m.AddressEvidence? = null,
    /**
     * 视觉源链，按优先级排。默认"云端 VLM → 回放 → 人工"。
     * 云端挂了会自动往后退，演示不会整场哑掉（任务书第 19 节）。
     * 传空列表 = 只用本地 OCR + 人工，等价于把 VLM 关掉。
     */
    extraReasoners: List<VisionReasoner> = emptyList(),
    /** 关掉就只剩本地 OCR + 人工 + 回放：现场没网也能演。 */
    val cloudEnabled: Boolean = true
) {

    /** 会话身份。采集数据与证据裁决都带着它，跨会话污染才能被发现并拒绝。 */
    val sessionId: String = "s${SESSION_SEQ.incrementAndGet()}-${SystemClock.elapsedRealtime()}"

    /**
     * 只保留 applicationContext。会话会被放进 `CameraActivity.last10mSession` 这个静态字段，
     * 万一 Activity 先销毁而会话还挂着，持有 Activity context 就是一条真实的生命周期泄漏。
     */
    private val appContext = context.applicationContext

    private val machine = Last10mStateMachine(profile, sessionId = sessionId)
    private val frameIds = AtomicLong(0L)
    @Volatile private var lastResult: Last10mStateMachine.Result? = null
    @Volatile private var lastHits: List<TextHit> = emptyList()
    @Volatile private var lastOcrUs: Long = 0L
    @Volatile private var closed = false
    @Volatile private var targetJustLost = false

    private val speech = Last10mSpeechPolicy()
    @Volatile private var lastVerdict: SpeechVerdict? = null

    private val ocr = OcrEngine(appContext, minIntervalMs = 300L, latinEnabled = false,
        events = { line -> logD(line) }) { hits, frameId, tsMs, us ->
        onOcrResults(hits, frameId, tsMs, us)
    }

    /** 视觉源链：云端 VLM 在前，注入的回放/人工源在后。 */
    private val reasoners: List<VisionReasoner> = buildList {
        addAll(extraReasoners)
        if (cloudEnabled) add(DeepSeekVisionReasoner(appContext))
    }

    private val orchestrator = PerceptionOrchestrator(
        machine = machine, profile = profile,
        relationEngine = TargetDoorRelationEngine(),
        reasoners = reasoners,
        clock = { now() },
        minIntervalMs = VLM_MIN_INTERVAL_MS,
        events = { line -> logW(line) },
        frameSize = { frameWidth to frameHeight })

    /**
     * 云端请求的闸门，复用 OCR 那套语义：节流、不排队、超时放闸、迟到结果作废。
     * 网络比 OCR 更容易卡，这里绝不能再开一条"排队等旧答案"的路。
     */
    private val vlmGate = OcrGate(VLM_MIN_INTERVAL_MS, VLM_TIMEOUT_MS, clock = { now() }) { line -> logD(line) }
    private val vlmExecutor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "BlindNavVlm").apply { isDaemon = true }
    }
    @Volatile private var visionInFlight = false
    @Volatile private var lastVisionTrail = "-"
    @Volatile private var lastVisionSource = "-"
    @Volatile private var lastVisionLatency = 0L
    @Volatile private var visionAttempts = 0L
    @Volatile private var visionFailures = 0L
    /** 用户主动问"前面有什么"时的回答；与状态提示分开，避免两条语音互相压。 */
    @Volatile private var lastSceneAnswer: String? = null

    val state: Last10mState get() = lastResult?.state ?: Last10mState.APPROACH
    val decision: ArrivalDecision get() = lastResult?.decision ?: ArrivalDecision.KEEP_SEARCHING

    init {
        machine.events = { line -> logW(line) }
        addressExpectation?.let { machine.setAddressExpectation(it) }
    }

    fun start() {
        machine.setInRange(true, now())
        logW("SESSION_START session=$sessionId ${profile.describe()}")
        refresh(now())
    }

    /** CameraX 帧入口。传入的 bitmap 归调用方，本类内部自己拷贝。 */
    fun onFrame(source: Bitmap, headingDegrees: Double?, upright: Boolean) {
        if (closed) return
        machine.setCameraHeading(headingDegrees, upright)
        lastHeading = headingDegrees
        val frameId = frameIds.incrementAndGet()
        val ts = now()
        val copy = runCatching { source.copy(Bitmap.Config.ARGB_8888, false) }.getOrNull() ?: return
        val previous = synchronized(frameLock) {
            lastFrame.also { lastFrame = copy }
        }.also { frameWidth = copy.width; frameHeight = copy.height }
        previous?.recycle()
        logD("FRAME_ACCEPTED session=$sessionId frame=$frameId ts=$ts " +
            "size=${copy.width}x${copy.height} heading=${headingDegrees?.let { "%.0f".format(it) } ?: "-"}")
        val accepted = ocr.submit(copy, frameId, ts)
        if (!accepted) {
            logD("FRAME_DROPPED session=$sessionId frame=$frameId reason=ocr_gate")
            copy.recycle()
        }
    }

    private val frameLock = Any()
    @Volatile private var lastFrame: Bitmap? = null
    @Volatile private var lastHeading: Double? = null
    @Volatile private var frameWidth: Int = 0
    @Volatile private var frameHeight: Int = 0

    /** 关系数据采集器；不挂就只是纯软件链路，不写盘。 */
    @Volatile var captureStore: Last10mCaptureStore? = null

    /**
     * 采集当前画面到 Last10m Capture。带上本会话身份，
     * 账本发现与当前组的 session 不一致会直接拒绝（防 A 店招牌配 B 店的门）。
     */
    fun capture(kind: String): Boolean {
        val store = captureStore ?: return false
        val frame = synchronized(frameLock) { lastFrame } ?: return false
        val copy = runCatching { frame.copy(Bitmap.Config.ARGB_8888, false) }.getOrNull() ?: return false
        val entry = store.captureCameraX(kind, copy, frameIds.get(), lastHeading, sessionId)
        copy.recycle()
        return entry != null
    }

    /**
     * 调试面板的三个分区按钮之一。框按**当前帧真实尺寸**换算。
     * 没有可用帧（还没打开看路）就返回 false，并说明原因——不退回任何默认尺寸。
     */
    fun markDebugDoor(position: DebugDoorPosition): Boolean {
        val w = frameWidth
        val h = frameHeight
        if (w <= 0 || h <= 0) {
            logW("DOOR_REJECTED reason=NO_FRAME position=$position —— 请先打开看路让相机出一帧")
            return false
        }
        val frameId = frameIds.get()
        val ts = now()
        machine.onDebugDoor(position, w, h, frameId, ts, ts)
        refresh(ts)
        logW("DEBUG_DOOR session=$sessionId ${DebugDoorProposal.describe(position, w, h)} frame=$frameId")
        return true
    }

    /** 旧接口保留给真实门检测接入时使用；人工标注一律走 [markDebugDoor]。 */
    fun onDoorDetected(left: Int, top: Int, right: Int, bottom: Int): Last10mStateMachine.Result {
        val frameId = frameIds.get()
        val ts = now()
        return machine.onDoorDetected(left, top, right, bottom, frameId, ts, ts)
    }

    /** VLM 的结构化事实（JSON 由调用方解析成 Map 后进入核心，越界字段在核心里被丢弃）。 */
    fun onVlmFacts(fields: Map<String, Any?>): Last10mStateMachine.Result {
        val frameId = frameIds.get()
        val ts = now()
        return machine.onVlmFacts(
            com.blindnav.app.last10m.VlmFacts.fromFields(fields, frameId, ts), ts)
    }

    /**
     * 人工声明"门前区域已进入画面"。来源固定 `DEBUG_MANUAL`：
     * 它只能证明人看过，不能证明门前没有台阶/路缘/阻挡，更不能说"安全"。
     */
    fun onGroundEvidenceFromDebugManual(): Last10mStateMachine.Result {
        val ts = now()
        return machine.onGroundEvidence(
            GroundEvidence(true, false, false, false, frameIds.get(), ts,
                "人工声明：门前区域已进入画面", GroundSource.DEBUG_MANUAL, sessionId), null, ts)
    }

    fun onGroundEvidence(visible: Boolean, step: Boolean, curb: Boolean, obstruction: Boolean,
                         source: GroundSource = GroundSource.DEBUG_MANUAL) {
        val ts = now()
        machine.onGroundEvidence(
            GroundEvidence(visible, step, curb, obstruction, frameIds.get(), ts,
                if (source == GroundSource.DEBUG_MANUAL) "人工声明" else "", source, sessionId), null, ts)
        refresh(ts)
    }

    fun setRouteBearing(bearingDegrees: Double?) {
        machine.setRouteBearing(bearingDegrees)
        refresh(now())
    }

    /** STOP/危险级播报开始时通知本会话：接下来一段时间 P1 不许插嘴。 */
    fun noteSafetySpeech() {
        speech.noteHigherPriority(now())
        logD("SPEECH_SUPPRESSED session=$sessionId reason=stop_priority_window")
    }

    private fun onOcrResults(hits: List<TextHit>, frameId: Long, tsMs: Long, us: Long) {
        lastHits = hits
        lastOcrUs = us
        // 文字同时交给编排器：关系判定要用"最近的本地 OCR 命中 + 框"。
        orchestrator.onLocalTexts(hits, frameId, tsMs, now())
        var result: Last10mStateMachine.Result? = lastResult
        // 一帧没有任何文字命中也要 evaluate 一次：目标锁的过期是"时间到了"而不是"新证据来了"。
        val before = machine.targetSnapshot().status
        result = machine.evaluate(now(), hits.map { it.text })
        targetJustLost = before == com.blindnav.app.last10m.TargetLock.Status.TARGET_LOCKED &&
            !result.targetLocked
        lastResult = result
        logW("OCR frame=$frameId hits=${hits.size} us=$us locked=${result.targetLocked} " +
            "state=${result.state} decision=${result.decision} " +
            "texts=${hits.take(3).joinToString("|") { it.text.take(12) }}")
        if (speakPrompts) announce(result)
        maybeAskVision(frameId, tsMs, now())
    }

    /**
     * 低频云端请求：只有"缺信息"时才发，并且同一时刻只让一个请求在飞。
     * 帧的 JPEG 在提交那一刻就冻结好，避免后台线程读到一块已经被回收的 Bitmap。
     */
    private fun maybeAskVision(frameId: Long, tsMs: Long, nowMs: Long) {
        if (closed || reasoners.isEmpty() || visionInFlight) return
        if (!orchestrator.shouldAskNow(nowMs)) return
        val generation = vlmGate.tryBegin(frameId) ?: return
        val frame = synchronized(frameLock) { lastFrame }?.let {
            runCatching { it.copy(Bitmap.Config.ARGB_8888, false) }.getOrNull()
        } ?: run { vlmGate.finish(generation, frameId, 0L, false); return }
        visionInFlight = true
        visionAttempts++
        val jpeg = runCatching { DeepSeekVisionReasoner.encodeJpeg(frame) }
            .getOrNull()
        frame.recycle()
        if (jpeg == null || jpeg.isEmpty()) {
            visionInFlight = false
            vlmGate.finish(generation, frameId, 0L, false)
            logW("VISION_FAILED session=$sessionId reason=encode frame=$frameId")
            return
        }
        vlmExecutor.execute {
            var applied = false
            try {
                val captured = orchestrator.capture(jpeg, frameId, tsMs, now(), "AUTO")
                if (captured != null && now() - tsMs > VLM_MAX_EVIDENCE_AGE_MS) {
                    // 迟得太久的答案讲的是"几秒前的那一帧"，那时候的画面早就不是眼前这个了。
                    logW("VISION_STALE_DISCARDED session=$sessionId frame=$frameId " +
                        "age=${now() - tsMs}ms limit=${VLM_MAX_EVIDENCE_AGE_MS}ms")
                    vlmGate.finish(generation, frameId, 0L, true)
                    visionFailures++
                    return@execute
                }
                if (captured != null && vlmGate.finish(generation, frameId, 0L, true)) {
                    val outcome = orchestrator.deliver(captured, now())
                    lastVisionTrail = outcome.trail.joinToString(" → ") { "${it.source}:${it.outcome}" }
                    lastVisionSource = captured.source.label()
                    lastVisionLatency = captured.result.latencyMs
                    applied = true
                    val r = machine.evaluate(now(), emptyList())
                    lastResult = r
                    if (speakPrompts) announce(r)
                } else if (captured != null) {
                    logD("VISION_LATE_DISCARDED session=$sessionId frame=$frameId —— 结果赶不上当前帧，丢弃")
                    vlmGate.finish(generation, frameId, 0L, true)
                    applied = false
                }
            } catch (e: Throwable) {
                logW("VISION_FAILED session=$sessionId ${e.javaClass.simpleName}:${e.message?.take(60)}")
                vlmGate.finish(generation, frameId, 0L, false)
            } finally {
                if (!applied) visionFailures++
                visionInFlight = false
            }
        }
    }

    /**
     * 用户主动问"前面有什么"（任务书第 14 节第一目标）。
     * 走同一条源链，但跳过"缺信息才问"的触发条件：他问了就必须答。
     */
    fun querySceneNow(onAnswer: (String?) -> Unit) {
        val frame = synchronized(frameLock) { lastFrame }
        if (frame == null) { onAnswer(null); logW("SCENE_QUERY session=$sessionId reason=NO_FRAME"); return }
        val copy = runCatching { frame.copy(Bitmap.Config.ARGB_8888, false) }.getOrNull()
            ?: run { onAnswer(null); return }
        val jpeg = runCatching { DeepSeekVisionReasoner.encodeJpeg(copy) }.getOrNull()
        copy.recycle()
        val frameId = frameIds.get()
        val ts = now()
        vlmExecutor.execute {
            val answer = runCatching {
                val captured = jpeg?.let {
                    orchestrator.capture(it, frameId, ts, now(), "SCENE_QUERY", sceneQuestion = true)
                }
                captured?.let { c ->
                    orchestrator.deliver(c, now())
                    lastVisionSource = c.source.label()
                    lastVisionTrail = c.trail.joinToString(" → ") { "${it.source}:${it.outcome}" }
                    lastVisionLatency = c.result.latencyMs
                    c.result.sceneSummary
                }
            }.getOrNull()
            val text = answer ?: "暂时没看清前面有什么，请站稳再问一次。"
            lastSceneAnswer = text
            val verdict = speech.answerNow(text, now(), TtsEngine.stopLevelActive())
            if (verdict is SpeechVerdict.Speak) {
                logW("SPEECH_ENQUEUED session=$sessionId cue=SCENE_ANSWER reason=user_asked text=\"${verdict.text}\"")
                TtsEngine.speak(verdict.text)
            } else {
                logW("SPEECH_SUPPRESSED session=$sessionId cue=SCENE_ANSWER reason=${verdict.reason}")
            }
            onAnswer(text)
        }
    }

    fun refresh(nowMs: Long) {
        val before = machine.targetSnapshot().status
        val r = machine.evaluate(nowMs, emptyList())
        targetJustLost = before == com.blindnav.app.last10m.TargetLock.Status.TARGET_LOCKED && !r.targetLocked
        lastResult = r
        if (speakPrompts) announce(r)
    }

    /**
     * 播报裁决。真正的说话入口只有这一个：状态机给文本，[Last10mSpeechPolicy] 决定
     * 说不说，[TtsEngine] 决定怎么排队。三层各自都能在日志里对上号。
     */
    private fun announce(result: Last10mStateMachine.Result) {
        val ground = machine.candidates().firstOrNull { it.doorId == result.activeDoorId }?.groundEvidence
        val hazard = if (ground?.source == GroundSource.VLM || ground?.source == GroundSource.REPLAY)
            Last10mPrompts.forGroundHazard(ground.step, ground.curb, ground.obstruction) else null
        val multiDoor = result.candidateCount > 1
        val signal = Last10mSpeechCue.signal(result, sessionId,
            if (multiDoor) null else machine.doorType(),
            if (multiDoor) null else machine.buttonLocation(), hazard, targetJustLost)
        if (signal.cue == SpeechCue.NONE) return
        if (TtsEngine.stopLevelActive()) {
            speech.noteHigherPriority(now())
        }
        val verdict = speech.decide(signal, now())
        lastVerdict = verdict
        when (verdict) {
            is SpeechVerdict.Speak -> {
                logW("SPEECH_ENQUEUED session=$sessionId cue=${verdict.cue} " +
                    "reason=${verdict.reason} frame=${frameIds.get()} text=\"${verdict.text}\"")
                TtsEngine.speak(verdict.text)
            }
            is SpeechVerdict.Suppress -> {
                // nothing_changed 是绝大多数帧，单独用 debug 级，别让 warn 被淹掉
                if (verdict.reason == "nothing_changed") {
                    logD("SPEECH_SUPPRESSED session=$sessionId cue=${verdict.cue} reason=nothing_changed")
                } else {
                    logW("SPEECH_SUPPRESSED session=$sessionId cue=${verdict.cue} reason=${verdict.reason}")
                }
            }
        }
    }

    fun stats(): Last10mSessionStats = Last10mSessionStats(
        sessionId = sessionId,
        frameCount = frameIds.get(),
        ocrUs = lastOcrUs,
        ocr = ocr.statsLine(),
        speechSpoken = speech.spoken,
        speechSuppressed = speech.suppressed,
        lastSpeechText = speech.lastSpoken(),
        lastSpeechReason = speech.lastDecisionReason
    )

    /**
     * 给人看的三行：目标 / 现在 / 下一步（任务书第 18 节）。
     * reason code 与内部变量留给下面的调试区，这一屏不出现英文状态名。
     */
    fun headerText(): String {
        val r = lastResult ?: return "目标 ${profile.poiName}\n当前：等待第一帧"
        val sourceLine = if (lastVisionSource == "-")
            "来源：本地 OCR + 人工（未接视觉源）"
        else "来源：$lastVisionSource（${lastVisionLatency}ms）"
        return buildString {
            append("目标：").append(profile.poiName).append('\n')
            append("当前：").append(NowSay.of(r.state, r.decision, r.candidateCount)).append('\n')
            append("下一步：").append(r.viewPrompt.ifBlank { r.prompt }.ifBlank { "保持不动，继续观察" })
            if (r.decision == ArrivalDecision.ARRIVAL_CONFIRMED) {
                append('\n').append(Last10mPrompts.forDecision(r.decision, r.state))
            }
            append('\n').append(sourceLine)
            lastSceneAnswer?.let { append("\n画面：").append(it) }
        }
    }

    /** 调试屏用的一屏状态。不美化，只保证"系统在想什么"看得见。 */
    fun statusText(): String {
        val r = lastResult
        val snap = machine.targetSnapshot()
        val st = stats()
        val doors = machine.candidates()
        val active = doors.firstOrNull { it.doorId == r?.activeDoorId }
        return buildString {
            appendLine("目标 ${profile.poiName}  session=${st.sessionId}")
            appendLine("state=${r?.state ?: "APPROACH"} decision=${r?.decision ?: "-"}")
            appendLine("frame=${st.frameCount} ocrUs=${st.ocrUs}")
            // —— 身份：凭什么说这是目标
            appendLine("TargetLock=${snap.status} hits=${snap.hitCount} frames=${snap.distinctFrames} " +
                "score=%.2f".format(snap.score) + " match=${snap.lastKind} " +
                "aliasSrc=${snap.lastAliasSource ?: "-"}")
            appendLine("alias=${snap.matchedAlias.ifEmpty { "-" }} " +
                "supporting=${snap.supportingHits}(${snap.supportingAlias.ifEmpty { "-" }})")
            appendLine("firstSeen=${snap.firstSeenMs} lastSeen=${snap.lastSeenMs} sources=${snap.sources}")
            appendLine("weakAliases=${profile.weakAliases.joinToString("/").ifEmpty { "-" }}")
            // —— 入口候选：来自谁
            appendLine("candidates=${r?.candidateCount ?: 0} activeDoor=${r?.activeDoorId ?: "-"}")
            if (doors.isEmpty()) {
                appendLine("  (没有候选门：门检测尚未接入，人工按「标左/中/右门」才会产生)")
            } else {
                doors.forEach { c ->
                    appendLine("  ${c.doorId} src=${c.source} pos=${c.debugPosition ?: "-"} " +
                        "frame=${c.sourceFrameId} box=${c.left},${c.top},${c.right},${c.bottom} " +
                        "seen=${c.seenFrames} relation=${c.relationConfirmed ?: "未判"} " +
                        "relationSrc=${c.relationSource ?: "NONE"} session=${c.sessionId ?: "-"}")
                }
            }
            appendLine("doorSource=${active?.source ?: "NONE"} relationSource=${active?.relationSource ?: "NONE"}")
            // —— 地面与方向
            appendLine("ground=${r?.groundState ?: GroundState.NOT_OBSERVED} " +
                "groundSource=${active?.groundEvidence?.source ?: "NONE"} " +
                "step=${active?.groundEvidence?.step ?: false} curb=${active?.groundEvidence?.curb ?: false} " +
                "obstruction=${active?.groundEvidence?.obstruction ?: false}")
            appendLine("scan=${r?.scanDirection ?: ScanDirection.UNKNOWN} " +
                "scanReason=${r?.scanReason ?: "-"}")
            // —— 为什么还不确认 + 下一步看哪里
            appendLine("needView=${r?.needView ?: "-"} reasonCodes=${r?.reasonCodes?.joinToString("/") ?: "-"}")
            r?.reasonCodes?.firstOrNull { it.name != "NONE" }?.let {
                appendLine("  为什么不确认：${com.blindnav.app.last10m.NeedViewPlanner.describe(it)}")
            }
            appendLine("viewPrompt=\"${r?.viewPrompt ?: ""}\" prompt=\"${r?.prompt ?: ""}\"")
            // —— OCR 与播报的运行状况
            appendLine(st.ocr)
            appendLine("speech spoken=${st.speechSpoken} suppressed=${st.speechSuppressed} " +
                "last=\"${st.lastSpeechText.ifEmpty { "-" }}\" why=${st.lastSpeechReason}")
            appendLine("lastTexts=${lastHits.take(4).joinToString(" | ") { it.text.take(14) }}")
            if (!r?.violations.isNullOrEmpty()) appendLine("VLM越界字段=${r?.violations}")
            appendLine("capture=${captureStore?.statusForPanel(sessionId) ?: "未挂载采集器"}")
            // —— 统一视觉结果：每一项都带来源，避免"回放"被听成"实时识别"
            val v = machine.lastVision
            appendLine("vision=${v?.source?.label() ?: "-"} attempts=$visionAttempts fails=$visionFailures " +
                "latency=${lastVisionLatency}ms chain=${lastVisionTrail.take(90)}")
            if (v != null) {
                appendLine("  doorType=${v.doorType}(${v.provenance["doorType"] ?: "-"}) " +
                    "button=${v.buttonLocation}(${v.provenance["button"] ?: "-"})")
                v.sceneSummary?.let { appendLine("  scene=\"$it\"") }
            }
        }
    }

    private fun logW(line: String) { if (!closed) Log.w(TAG, line) }
    private fun logD(line: String) { if (!closed) Log.d(TAG, line) }

    fun close() {
        closed = true
        vlmExecutor.shutdownNow()
        reasoners.forEach { runCatching { it.close() } }
        ocr.close()
        synchronized(frameLock) { lastFrame?.recycle(); lastFrame = null }
        CameraActivity.last10mSession = null
        logW("SESSION_END session=$sessionId frames=${frameIds.get()} " +
            "spoken=${speech.spoken} state=${lastResult?.state}")
    }

    private fun now(): Long = SystemClock.elapsedRealtime()

    private companion object {
        const val TAG = "BlindNavLast10m"
        /** 云端一次约 1.5–2.5s；2.5s 的间隔既是节流也是"别把用户问晕"。 */
        const val VLM_MIN_INTERVAL_MS = 2_500L
        /** 工程看门狗：一次云端请求最多等多久。不是算法延迟指标。 */
        const val VLM_TIMEOUT_MS = 12_000L
        /**
         * 一次视觉答案最多能"老"到多久。
         * 看门狗按 12s 放闸，但一个 5s 前那帧的答案已经不该影响现在的判断了。
         */
        const val VLM_MAX_EVIDENCE_AGE_MS = 4_000L

        /** 会话身份在进程内递增，足够区分同一台手机上的前后两段会话。 */
        val SESSION_SEQ = AtomicLong(0L)
        const val GroundState_NOT_OBSERVED = "NOT_OBSERVED"
    }
}
