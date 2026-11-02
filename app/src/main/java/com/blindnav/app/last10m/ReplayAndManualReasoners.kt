package com.blindnav.app.last10m

/**
 * 回放源（任务书第 5 节第二选择 / 第 16 节 Demo Mode 的基础）。
 *
 * 读的是电脑阶段**真实跑出来**的结果：从公开数据集图像上跑的 VLM 结构化读数、
 * OWL / Grounding DINO 的门候选、关系判定与地面判读。
 * 这些数字在报告里从没被当成"真机能力"，但用来把整条链演示通是合格的——
 * 前提是每一帧都标 `REPLAY`，面板/日志/TTS 都不许把它说成实时识别。
 *
 * 解析走的是生产同一个 [VisionResultParser]，所以回放帧与真实 VLM 帧
 * 在下游完全同构：同一套代码路径，不存在"演示专用假实现"。
 */
class ReplayVisionReasoner(
    frames: List<ReplayFrame>,
    private val loop: Boolean = true
) : VisionReasoner {

    data class ReplayFrame(val label: String, val json: String)

    private val parsed = frames.map { it to VisionResultParser.parse(
        it.json, frameId = 0L, timestampMs = 0L, source = EvidenceSource.REPLAY, latencyMs = 0L) }
    private var cursor = 0

    override val source: EvidenceSource = EvidenceSource.REPLAY

    val frameCount: Int get() = parsed.size
    val currentIndex: Int get() = cursor
    val currentLabel: String get() = parsed[cursor].first.label

    fun advance(): Int {
        cursor = if (cursor + 1 >= parsed.size) (if (loop) 0 else cursor) else cursor + 1
        return cursor
    }

    fun rewind(): Int {
        cursor = if (cursor == 0) (if (loop) parsed.size - 1 else 0) else cursor - 1
        return cursor
    }

    fun goTo(index: Int): Int {
        if (parsed.isEmpty()) return 0
        cursor = index.coerceIn(0, parsed.size - 1)
        return cursor
    }

    /** 把这一帧"接"到当前会话的帧号/时间轴上；其余内容原样，来源仍是 REPLAY。 */
    override fun analyze(jpeg: ByteArray?, frameId: Long, timestampMs: Long,
                         target: TargetPoiProfile, context: VisionContext): Last10mVisionResult? {
        if (parsed.isEmpty()) return null
        val (_, result) = parsed[cursor]
        if (!result.parseOk) return result
        return result.copy(
            frameId = frameId, timestampMs = timestampMs,
            // 文本命中的帧号必须跟着改，否则 TargetLock 会把回放里的旧帧号当成"同一帧"
            texts = result.texts.map { it.copy(sourceFrameId = frameId, timestampMs = timestampMs) },
            provenance = result.provenance + ("note" to "REPLAY"))
    }

    /** 场景脚本里的原始 JSON，测试与排错用。 */
    fun rawJson(index: Int = cursor): String = parsed[index].first.json
}

/**
 * 人工兜底源（任务书第 5 节第三选择 / 第 7 节路线 C）。
 *
 * 人按出来的框不是识别结果，但它必须**以同一个结构**进入链路，
 * 这样"自动源挂了"的时候整条体验不断，而来源标记依然是 DEBUG_MANUAL。
 */
class DebugManualVisionReasoner(
    private val stateProvider: () -> ManualState
) : VisionReasoner {

    data class ManualState(
        val doors: List<Pair<DebugDoorPosition, IntArray>> = emptyList(),   // 像素框
        val groundVisible: Boolean? = null,
        val step: Boolean? = null,
        val curb: Boolean? = null,
        val obstruction: Boolean? = null,
        val doorType: DoorType = DoorType.UNKNOWN,
        val button: ButtonLocation = ButtonLocation.UNKNOWN,
        val sceneSummary: String? = null
    )

    override val source: EvidenceSource = EvidenceSource.DEBUG_MANUAL

    override fun analyze(jpeg: ByteArray?, frameId: Long, timestampMs: Long,
                         target: TargetPoiProfile, context: VisionContext): Last10mVisionResult? {
        val s = stateProvider()
        if (s.doors.isEmpty() && s.groundVisible == null && s.sceneSummary == null) return null
        val doors = s.doors.mapIndexed { i, (pos, box) ->
            VisionDoor("manual_${pos.name.lowercase()}", permilleOf(box, context.frameWidth, context.frameHeight), 1.0)
                .let { it to pos }
        }
        return Last10mVisionResult(
            frameId = frameId, timestampMs = timestampMs, source = EvidenceSource.DEBUG_MANUAL,
            doors = doors.map { it.first },
            ground = if (s.groundVisible == null) VisionGround.UNKNOWN else
                VisionGround(s.groundVisible, s.step, s.curb, s.obstruction),
            doorType = s.doorType, buttonLocation = s.button, sceneSummary = s.sceneSummary,
            provenance = buildMap {
                if (doors.isNotEmpty()) put("doors", "DEBUG_MANUAL")
                if (s.groundVisible != null) put("ground", "DEBUG_MANUAL")
            },
            parseOk = true)
    }

    private fun permilleOf(box: IntArray, width: Int, height: Int): IntArray {
        if (width <= 0 || height <= 0) return intArrayOf(0, 0, 0, 0)
        return intArrayOf(
            (box[0] * 1000 / width).coerceIn(0, 1000), (box[1] * 1000 / height).coerceIn(0, 1000),
            (box[2] * 1000 / width).coerceIn(0, 1000), (box[3] * 1000 / height).coerceIn(0, 1000))
    }
}
