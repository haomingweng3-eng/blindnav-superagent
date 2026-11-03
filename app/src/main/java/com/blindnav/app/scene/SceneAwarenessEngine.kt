package com.blindnav.app.scene

/**
 * 环境感知编排器（4+1 里的第 4 项 + "+1 用户主动问"）。
 *
 * 两条链并行、职责分开：
 *   快速安全链  端侧模型，每帧都跑，直接产出 URGENT。不依赖网络。
 *   环境理解链  低频/按需调云端 VLM，产出 AMBIENT 描述与 ANSWER 回答。
 *
 * 关键点是**不要每帧都问云端**。这里用一个"变化门"：
 * 只有本地目标集合相对上次云端描述发生了明显变化（或用户主动问、或到了兜底周期），
 * 才发一次云端请求。断网时环境描述自动降级为沉默，
 * 安全链、OCR、通道保持照旧工作——这是架构要求，不是错误处理。
 *
 * 与生产安全链的边界：这个类**不**调用 CameraActivity，也不直接 TTS，
 * 只返回"该说什么"。接线要等手机在手上有真帧率与真延迟时再做。
 */
interface LocalPerceptor {
    /** 端侧模型（YOLO 类 + OCR）对一帧的结构化读数。必须快，不能等网络。 */
    fun perceive(frameId: Long, timestampMs: Long): List<SceneObject>
    fun texts(frameId: Long): List<String> = emptyList()
}

/** 云端视觉描述。实现可以是火山方舟、DeepSeek 或任何 VLM；失败就返回 null。 */
interface SceneDescriber {
    fun describe(jpegBytes: ByteArray, question: String?): SceneObservation?
}

data class AwarenessConfig(
    /** 变化门：目标集合的 Jaccard 距离超过这个值才值得再问一次云端 */
    val changeThreshold: Double = 0.34,
    /** 云端请求的最短间隔，兜底防止高频 */
    val minCloudIntervalMs: Long = 6_000L,
    /** 即使没变化，最长多久也要重新看一眼（环境会缓慢变化） */
    val maxSilenceMs: Long = 25_000L,
    /** 刚拿到第一帧时是否立刻问一次云端 */
    val primeOnFirstFrame: Boolean = true
)

class SceneAwarenessEngine(
    private val local: LocalPerceptor,
    private val describer: SceneDescriber?,
    private val memory: SceneMemory = SceneMemory(),
    private val policy: SpeechPolicy = SpeechPolicy(),
    private val config: AwarenessConfig = AwarenessConfig()
) {
    private var lastCloudAt = Long.MIN_VALUE
    private var lastCloudSet: Set<String> = emptySet()
    private var primed = false
    private var frameCount = 0
    private var cloudCalls = 0
    private var cloudRejected = 0

    data class Output(val urgent: List<SpeechDecision>, val ambient: SpeechDecision)

    val stats: Map<String, Int> get() = mapOf(
        "frames" to frameCount, "cloud_calls" to cloudCalls, "cloud_gated_off" to cloudRejected)

    /**
     * 一帧进来。返回该不该说话。
     * @param jpeg 供云端用的编码帧；null 表示这帧不打算上传（只做本地链）
     */
    fun onFrame(frameId: Long, timestampMs: Long, jpeg: ByteArray?): Output {
        frameCount++
        val localObs = SceneObservation(frameId, timestampMs,
            local.perceive(frameId, timestampMs), local.texts(frameId), "LOCAL")
        val localDelta = memory.observe(localObs)

        val urgent = ArrayList<SpeechDecision>()
        for (o in localDelta.appeared.filter { it.category in SAFETY_CATEGORIES }) {
            urgent.add(policy.decide(SpeechRequest(SpeechPriority.URGENT,
                "${SceneNarrator.sideLabel(o.side)}有${SceneNarrator.phrase(o)}",
                setOf(o.category), timestampMs)))
        }
        for ((_, now) in localDelta.stateChanged.filter { it.second.category in SAFETY_CATEGORIES }) {
            urgent.add(policy.decide(SpeechRequest(SpeechPriority.URGENT,
                "${SceneNarrator.sideLabel(now.side)}的${SceneNarrator.phrase(now)}",
                setOf(now.category), timestampMs)))
        }

        if (!shouldAskCloud(timestampMs, localDelta)) {
            cloudRejected++
            return Output(urgent, SpeechDecision(SpeechAction.SILENT, "cloud_gated"))
        }
        val obs = describer?.describe(jpeg ?: ByteArray(0), null)
        if (obs == null) {
            // 云端不可用：环境描述降级为沉默，安全链不受影响
            return Output(urgent, SpeechDecision(SpeechAction.SILENT, "cloud_unavailable"))
        }
        lastCloudAt = timestampMs
        lastCloudSet = obs.objects.map { it.identity }.toSet()
        cloudCalls++
        val cloudDelta = memory.observe(obs.copy(frameId = frameId + 100_000_000L))
        val text = SceneNarrator.narrate(cloudDelta)
        val ambient = policy.decide(SpeechRequest(SpeechPriority.AMBIENT, text,
            (cloudDelta.appeared + cloudDelta.stateChanged.map { it.second }).map { it.category }.toSet(),
            timestampMs, hasNews = cloudDelta.hasNews && text.isNotBlank()))
        return Output(urgent, ambient)
    }

    /** 用户主动问"前面有什么"。这条绕开变化门与 AMBIENT 节流。 */
    fun ask(question: String, jpeg: ByteArray?, frameId: Long, timestampMs: Long): SpeechDecision {
        val obs = describer?.describe(jpeg ?: ByteArray(0), question)
            ?: return SpeechDecision(SpeechAction.SILENT, "cloud_unavailable")
        lastCloudAt = timestampMs
        lastCloudSet = obs.objects.map { it.identity }.toSet()
        cloudCalls++
        memory.observe(obs.copy(frameId = frameId + 100_000_000L))
        val text = SceneNarrator.narrate(SceneDelta(
            appeared = obs.objects, stateChanged = emptyList(),
            disappeared = emptyList(), newTexts = obs.texts))
        val merged = if (obs.texts.isNotEmpty() && text.isNotBlank())
            "$text；读到「${obs.texts.first().take(12)}」" else text
        return policy.decide(SpeechRequest(SpeechPriority.ANSWER,
            merged.ifBlank { "没看到值得说的东西" }, emptySet(), timestampMs))
    }

    private fun shouldAskCloud(nowMs: Long, delta: SceneDelta): Boolean {
        if (describer == null) return false
        if (!primed) {
            primed = true
            return config.primeOnFirstFrame
        }
        if (nowMs - lastCloudAt < config.minCloudIntervalMs) return false
        if (nowMs - lastCloudAt >= config.maxSilenceMs) return true
        if (!delta.hasNews) return false
        val now = delta.appeared.map { it.identity }.toSet() +
            delta.stateChanged.map { it.second.identity }
        val union = lastCloudSet + now
        val jaccard = if (union.isEmpty()) 0.0 else now.size.toDouble() / union.size
        return jaccard >= config.changeThreshold
    }

    fun reset() {
        memory.reset(); policy.reset(); lastCloudSet = emptySet()
        lastCloudAt = Long.MIN_VALUE; primed = false
    }

    companion object {
        /** 只有这几类值得走 URGENT 通道；其余进 AMBIENT，慢一点无所谓。 */
        val SAFETY_CATEGORIES = setOf("person", "bicycle", "car", "motorcycle",
            "obstacle", "stairs", "curb", "pole", "door_opening")
    }
}
