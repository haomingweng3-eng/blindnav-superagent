package com.blindnav.app.last10m

/**
 * 最后十米用到的证据类型。
 *
 * 三条硬规矩，都是为了让"证据过期"这件事无法被悄悄忽略：
 *  1. 每条证据自带 `sourceFrameId` 与 `timestampMs`，判定层不许用"现在"去代替"它当时"；
 *  2. 时钟由调用方注入（测试里是假时钟），所以 replay 结果可复现；
 *  3. `UNKNOWN` 是一等公民：地面没看到就是 UNKNOWN，不等于 SAFE。
 */

/**
 * 证据来源。
 *
 * 旧的四档（OCR/VLM/GROUND_MODEL/MANUAL）保留，因为关系判定与既有测试都在用它们；
 * 今晚新加的五个名字是**统一视觉结果**的来源口径（任务书第 4 节）：
 *  - LOCAL_OCR     手机本地 bundled ML Kit；
 *  - LOCAL_MODEL   手机本地视觉模型（YOLO 门类等）；
 *  - REMOTE_VLM    云端 VLM（一次调用给多项事实）；
 *  - REPLAY        离线回放（demo / 调试），不是实时感知；
 *  - DEBUG_MANUAL  人在面板上按出来的，只是声明，不是识别。
 * 三者的可信度完全不同，所以必须一路带到 UI 和 TTS，不能合并成"看到了"。
 */
enum class EvidenceSource {
    OCR, VLM, GROUND_MODEL, MANUAL,
    LOCAL_OCR, LOCAL_MODEL, REMOTE_VLM, REPLAY, DEBUG_MANUAL
}

/** 来源的中文口径，面板/日志/TTS 说明统一用它，避免同一件事有三种说法。 */
fun EvidenceSource.label(): String = when (this) {
    EvidenceSource.OCR, EvidenceSource.LOCAL_OCR -> "LOCAL_OCR"
    EvidenceSource.LOCAL_MODEL -> "LOCAL_MODEL"
    EvidenceSource.VLM, EvidenceSource.REMOTE_VLM -> "REMOTE_VLM"
    EvidenceSource.REPLAY -> "REPLAY"
    EvidenceSource.MANUAL, EvidenceSource.DEBUG_MANUAL -> "DEBUG_MANUAL"
    EvidenceSource.GROUND_MODEL -> "GROUND_MODEL"
}

/**
 * VLM 给的都算**临时**结论：今晚它只是把链路串起来的 prototype 来源，
 * 没有真值背书。所以地面/关系这类字段要显示成 `REMOTE_VLM_PROVISIONAL`，
 * 不能说成"已验证"。
 */
fun EvidenceSource.provisionalLabel(): String = when (this) {
    EvidenceSource.VLM, EvidenceSource.REMOTE_VLM -> "REMOTE_VLM_PROVISIONAL"
    EvidenceSource.REPLAY -> "REPLAY_PROVISIONAL"
    else -> label()
}

/**
 * 入口候选是怎么来的。这条单独建枚举是为了让"人工点的框"和"模型检出的框"
 * 在面板、日志和采集数据里永远能分开——上一轮真机审计就是因为两者混在一起，
 * 才导致"人点两下 = 系统自动找到入口"这种说法有机会出现。
 */
enum class CandidateSource {
    /** 调试面板「标左/中/右门」按出来的框。人工声明，不是检测结果。 */
    DEBUG_MANUAL_PROPOSAL,

    /** 真实门检测（OWL / Grounding DINO / YOLO 门类）。 */
    MODEL,

    /** VLM 结构化事实给出的候选框。 */
    VLM,

    /** 离线回放里的既有结果。演示可用，但绝不能被读成"实时检测到了门"。 */
    REPLAY,

    /** 未知来源：宁可标未知，也不假装是模型给的。 */
    UNKNOWN
}

/** 人工标注时用的三个画面分区。坐标一律来自**当前帧真实尺寸**，不来自任何 View 尺寸。 */
enum class DebugDoorPosition { LEFT, CENTER, RIGHT }

/** 门前地面证据是怎么来的。`DEBUG_MANUAL` 只代表"人看了一眼"，不代表"模型确认安全"。 */
enum class GroundSource { DEBUG_MANUAL, GROUND_MODEL, VLM, REPLAY, UNKNOWN }

/** 一次文字识别命中。 */
data class TextHit(
    val text: String,
    val left: Int,
    val top: Int,
    val right: Int,
    val bottom: Int,
    val confidence: Double,
    val sourceFrameId: Long,
    val timestampMs: Long,
    val source: EvidenceSource = EvidenceSource.OCR
) {
    val center: Pair<Double, Double>
        get() = Pair((left + right) / 2.0, (top + bottom) / 2.0)
}

/** 一个入口候选。`doorId` 由 EntranceTracker 维护，代表"跨帧认为是同一扇门"。 */
data class EntranceCandidate(
    val doorId: String,
    var left: Int,
    var top: Int,
    var right: Int,
    var bottom: Int,
    val firstSeenMs: Long,
    var lastSeenMs: Long,
    var seenFrames: Int,
    /** 与目标招牌是否被判定属于同一店面；null = 还没判过。 */
    var relationConfirmed: Boolean? = null,
    var relationSource: EvidenceSource? = null,
    /** 关系结论是哪一刻做的。云端 VLM / 回放这类临时来源必须查新鲜度。 */
    var relationEvidenceMs: Long = 0L,
    /** 门前地面是否已进入画面并可判读。 */
    var groundObserved: Boolean = false,
    var groundEvidence: GroundEvidence? = null,
    /** 最近一次写入这个候选的帧号，面板与日志要能回答"这个框是哪一帧来的"。 */
    var sourceFrameId: Long = -1L,
    /** 候选来源。人工按出来的必须是 DEBUG_MANUAL_PROPOSAL，不能冒充模型结论。 */
    var source: CandidateSource = CandidateSource.UNKNOWN,
    /** 仅人工标注时有值：来自画面哪个分区。 */
    var debugPosition: DebugDoorPosition? = null,
    /** 产生这个候选的会话。跨会话证据必须在裁决处被拒（见 ArrivalAuthority）。 */
    val sessionId: String? = null
) {
    /**
     * 必须走文件底部那个共享 `ageMs`：时钟回退时它返回 Long.MAX_VALUE，
     * 而裸减法会得到**负数**，负数能骗过所有"小于阈值即新鲜"的判断。
     * 之前这里就是裸减法，fuzz 用 `lastSeenMs = now + 5000` 直接造出了
     * "证据时间戳在未来 → 依然 ARRIVAL_CONFIRMED" 的反例。
     */
    fun ageMs(nowMs: Long): Long = ageMs(nowMs, lastSeenMs)
}

/** 门前地面证据。`visible=false` 时其它字段无意义，状态必须走 UNKNOWN。 */
data class GroundEvidence(
    val visible: Boolean,
    val step: Boolean,
    val curb: Boolean,
    val obstruction: Boolean,
    val sourceFrameId: Long,
    val timestampMs: Long,
    val note: String = "",
    /** 这条地面证据是谁给的。人工长按 = DEBUG_MANUAL，只能记成"已看过"。 */
    val source: GroundSource = GroundSource.UNKNOWN,
    /** 产生这条证据的会话，跨会话不允许背书。 */
    val sessionId: String? = null
) {
    companion object {
        /** 没看到地面就是 UNKNOWN，绝不能构造一个"看起来安全"的默认值。 */
        fun unknown(frameId: Long, tsMs: Long): GroundEvidence =
            GroundEvidence(false, false, false, false, frameId, tsMs, "门前区域未进入画面")
    }
}

/** 扫描方向。只用于"朝哪边看"，**不是**运动指令。 */
enum class ScanDirection { LEFT, FRONT, RIGHT, UNKNOWN }

/** 关系判定结果。CONFLICT 是专门给"招牌是 A、门是隔壁"这种情况的。 */
enum class RelationOutcome { UNKNOWN, LIKELY_SAME, CONFLICT }

/** 时间工具：证据龄。负值（时钟回拨/跨会话）一律视为已过期，不做绝对值。 */
fun ageMs(nowMs: Long, thenMs: Long): Long = if (thenMs > nowMs) Long.MAX_VALUE else nowMs - thenMs
