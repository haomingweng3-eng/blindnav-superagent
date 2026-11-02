package com.blindnav.app.last10m

/**
 * 最后十米的统一视觉结果（任务书第 4 节）。
 *
 * 设计上有三条硬要求：
 *
 *  1. **一个结构、一条流水线**。今晚不再让 OCR / 门检测 / VLM / 地面 / 门类型各写一套接口，
 *     否则后面每加一个能力都要改一遍链路。所有视觉产出都归到 [Last10mVisionResult]，
 *     状态机只吃这一个结构。
 *  2. **每一项都能表达"不知道"**。所有事实字段都是可空的，
 *     `null` 就是 null —— 不许用 `false` 冒充"确认没有"，
 *     也不许用空列表冒充"确认没有门"（空列表在语义上是"这一源没给"）。
 *     一旦把"没看到"写成"没有"，系统就会拿着一个不存在的结论往下推。
 *  3. **来源永远跟着结果**。[source] 与每一项自己的 [provenance] 都要能显示出来，
 *     人按的、VLM 猜的、回放喂的、本地模型出的，绝不能混成同一种"系统看到了"。
 *
 * 这一层仍然**不做任何决策**：到达、运动、安全结论都不在这里产生，
 * 那是 [Last10mStateMachine] 与 [ArrivalAuthority] 的职责。
 */

/** 门类型（任务书第 10 节）。UNKNOWN 就什么都不说，不猜。 */
enum class DoorType {
    AUTOMATIC_SLIDING, PUSH_PULL, REVOLVING, OPEN_DOORWAY, UNKNOWN
}

/** 开门按钮位置（任务书第 11 节）。只在已经锁定入口附近时才问。 */
enum class ButtonLocation {
    LEFT_OF_DOOR, RIGHT_OF_DOOR, ON_DOOR, UNKNOWN
}

/** 一个候选门。坐标用千分比（0–1000，左上原点），换算到像素时再乘帧尺寸。 */
data class VisionDoor(
    val id: String,
    val boxPermille: IntArray,
    val confidence: Double?
) {
    fun boxInFrame(width: Int, height: Int): IntArray? {
        if (width <= 0 || height <= 0) return null
        val left = (boxPermille[0] * width / 1000)
        val right = (boxPermille[2] * width / 1000)
        val top = (boxPermille[1] * height / 1000)
        val bottom = (boxPermille[3] * height / 1000)
        if (right <= left || bottom <= top) return null
        return intArrayOf(left, top, right, bottom)
    }
}

/** 视觉源对"这扇门是不是目标家"的看法（最终结论仍由 [TargetDoorRelationEngine] 判）。 */
data class VisionRelation(
    val doorId: String,
    val relation: RelationOutcome,
    val confidence: Double?
)

/** 门前地面事实。每一项单独可空：没判 = null，不是 false。 */
data class VisionGround(
    val visible: Boolean? = null,
    val step: Boolean? = null,
    val curb: Boolean? = null,
    val obstruction: Boolean? = null
) {
    companion object {
        val UNKNOWN = VisionGround(null, null, null, null)
    }
}

/**
 * 统一视觉结果。
 *
 * [parseOk] 为 false 时代表"这一源的输出根本没法信"（JSON 坏了 / 字段越界 / 说漏了运动结论），
 * 此时其余字段必须全部是空/null —— 调用方不能拿半份结果继续判。
 */
data class Last10mVisionResult(
    val frameId: Long,
    val timestampMs: Long,
    val source: EvidenceSource,
    val texts: List<TextHit> = emptyList(),
    val doors: List<VisionDoor> = emptyList(),
    val targetVisible: Boolean? = null,
    val targetIdentity: String? = null,
    val targetConfidence: Double? = null,
    val relations: List<VisionRelation> = emptyList(),
    val ground: VisionGround = VisionGround.UNKNOWN,
    val doorType: DoorType = DoorType.UNKNOWN,
    val buttonLocation: ButtonLocation = ButtonLocation.UNKNOWN,
    val sceneSummary: String? = null,
    val needViewHint: String? = null,
    /** 每个字段的出处（"doors=REMOTE_VLM ground=REMOTE_VLM_PROVISIONAL"），面板与日志都要显示。 */
    val provenance: Map<String, String> = emptyMap(),
    val parseOk: Boolean = true,
    val violations: List<String> = emptyList(),
    val latencyMs: Long = 0L
) {
    val hasAnything: Boolean
        get() = parseOk && (texts.isNotEmpty() || doors.isNotEmpty() ||
            targetVisible != null || sceneSummary != null)

    companion object {
        /** "这一次什么都没拿到"。解析失败/超时/网络错都走这里，绝不半猜半用。 */
        fun unknown(frameId: Long, timestampMs: Long, source: EvidenceSource,
                    reason: String): Last10mVisionResult = Last10mVisionResult(
            frameId = frameId, timestampMs = timestampMs, source = source,
            parseOk = false, violations = listOf(reason)
        )
    }
}

/** 调用视觉源时带上已有的上下文，让 VLM 能一次把多件事答完（任务书第 6 节）。 */
data class VisionContext(
    val targetName: String,
    val targetAliases: List<String> = emptyList(),
    val recentTexts: List<String> = emptyList(),
    val targetLocked: Boolean = false,
    val knownDoorBoxes: List<VisionDoor> = emptyList(),
    val wantsSceneDescription: Boolean = false,
    val focusOnActiveDoor: Boolean = false,
    val frameWidth: Int = 0,
    val frameHeight: Int = 0
)
