package com.blindnav.app.last10m

/**
 * 该看哪里（能力 7）+ 为什么还不能确认（能力 12）。
 *
 * 设计上有两条硬约束：
 *  1. **只由缺失的证据决定**，输入是状态机自己算出来的量，VLM 不参与选动作。
 *     VLM 可以报 need_view 作为参考，但不能决定系统让用户看哪里——
 *     否则一次幻觉就是一次错误指引。
 *  2. 输出的永远是**扫描/观察**指令，绝不是移动指令。
 *     "请缓慢向右扫描"可以；"往右走"不行（这条由 [VlmFacts] 的黑名单和这里的枚举共同保证）。
 *
 * 优先级从"最不可原谅"往下排：
 * 地址/地标/关系冲突 → 目标本身没确认 → 没有门 → 多门分不清 → 关系没判 → 地面没看。
 * 冲突排最前，是因为把隔壁的入口指给用户，比让用户多扫两秒严重得多。
 */
enum class ReasonCode {
    NONE,
    TARGET_NOT_FOUND,       // 一次都没认出来
    TARGET_LOST,            // 认出来过，现在丢了
    TARGET_UNSTABLE,        // 还锁着，但身份证据正在变旧
    EVIDENCE_STALE,         // 证据过期
    DOOR_NOT_FOUND,         // 目标确认了，一个入口候选都没有
    MULTIPLE_DOORS,         // 多个候选，分不清哪扇
    RELATION_UNKNOWN,       // 招牌与门的关系还没判
    NEIGHBOR_CONFLICT,      // 邻门冲突
    RELATION_CONFLICT,      // 已判过：这扇不属于目标
    ADDRESS_CONFLICT,       // 门牌/楼号与目标不一致
    LANDMARK_CONFLICT,      // 看到与目标互斥的地标
    GROUND_NOT_VISIBLE,     // 门前地面没进画面
    GROUND_UNKNOWN,         // 看了但判读不了
    GROUND_STALE,           // 地面证据已过期，必须重新看一次
    GROUND_OBSTRUCTED,      // 门前有阻挡
    SESSION_MISMATCH        // 证据来自另一个会话，绝不参与本会话判定
}

enum class NeedView {
    NONE, HOLD_STILL, SCAN_LEFT, SCAN_RIGHT, SCAN_WIDER,
    LOOK_UP_SIGN, LOOK_AT_DOOR, LOOK_DOWN_GROUND, REOBSERVE_TARGET
}

/**
 * 方向算不出来时的原因。面板单独显示这一项，是为了避免现场把
 * "高德路线方位还没接入"误读成"ScanCoach 坏了"。
 */
enum class ScanUnknownReason {
    NONE,                       // 方向有效，没有未知要解释
    ROUTE_BEARING_MISSING,      // 还不知道目标在哪个方位
    HEADING_MISSING,            // 相机朝向不可用
    PHONE_NOT_UPRIGHT,          // 手机没拿正，罗盘读数没意义
    SENSOR_VALUE_INVALID        // 数值不是有限值
}

/**
 * 门前地面的状态。UNKNOWN ≠ SAFE，这条单独建类型是为了不让它被当成 false 混过去。
 *
 * 三种"已看见"必须分开，因为它们的可信度完全不同：
 *  - `OBSERVED_MANUAL`     人按了一下"门前地面已看"。只证明人看过。
 *  - `OBSERVED_PROVISIONAL` VLM / 回放说"地面在画面里"。是临时结论，没有真值背书。
 *  - `CLEAR`               本地地面模型看过，且台阶/路缘/阻挡都明确为否。
 * 把前两种显示成 SAFE，就等于让一次长按或一次幻觉冒充成一次安全确认。
 */
enum class GroundState {
    NOT_OBSERVED, UNKNOWN, BLOCKED, OBSERVED_MANUAL, OBSERVED_PROVISIONAL, CLEAR
}

/** 地面状态的唯一算法。之前状态机和 EvidenceSnapshot 各写了一份，容易走偏。 */
fun groundStateOf(e: GroundEvidence?): GroundState = when {
    e == null -> GroundState.NOT_OBSERVED
    !e.visible -> GroundState.UNKNOWN
    e.obstruction -> GroundState.BLOCKED
    e.source == GroundSource.DEBUG_MANUAL -> GroundState.OBSERVED_MANUAL
    e.source == GroundSource.VLM || e.source == GroundSource.REPLAY -> GroundState.OBSERVED_PROVISIONAL
    e.step || e.curb -> GroundState.OBSERVED_PROVISIONAL     // 模型看到了风险，不能说 CLEAR
    else -> GroundState.CLEAR
}

data class EvidenceSnapshot(
    val targetLocked: Boolean,
    val targetEverLocked: Boolean,
    val targetAgeMs: Long,
    val doorCandidates: Int,
    val hasActiveDoor: Boolean,
    /** null = 还没判过关系 */
    val relationConfirmed: Boolean?,
    val ground: GroundState,
    val neighborConflict: Boolean,
    val relationConflict: Boolean,
    val addressConflict: Boolean,
    val landmarkConflict: Boolean,
    val scan: ScanDirection,
    /** 地面证据有多旧。超过 [NeedViewPlanner.MAX_GROUND_AGE_MS] 就必须重新看一次。 */
    val groundAgeMs: Long = 0L,
    val scanReason: ScanUnknownReason = ScanUnknownReason.NONE,
    val groundSource: GroundSource = GroundSource.UNKNOWN,
    /** 活跃候选/地面证据是否来自当前会话。false 表示跨会话污染。 */
    val sameSession: Boolean = true
) {
    companion object {
        /** 从状态机已有的量拼一个快照，避免两处各算一套。 */
        fun of(s: Last10mStateMachine, nowMs: Long): EvidenceSnapshot {
            val snap = s.targetSnapshot()
            val active = s.candidates().firstOrNull { it.doorId == s.activeDoorId() }
            val ground = active?.groundEvidence
            return EvidenceSnapshot(
                targetLocked = snap.status == TargetLock.Status.TARGET_LOCKED,
                targetEverLocked = snap.everLocked,
                targetAgeMs = if (snap.lastSeenMs > 0) ageMs(nowMs, snap.lastSeenMs) else Long.MAX_VALUE,
                doorCandidates = s.candidates().size,
                hasActiveDoor = active != null,
                relationConfirmed = active?.relationConfirmed,
                ground = groundStateOf(ground),
                neighborConflict = s.neighborConflict(nowMs),
                relationConflict = s.relationConflict(),
                addressConflict = s.addressConflict(),
                landmarkConflict = s.landmarkConflict(),
                scan = s.scanDirection(),
                groundAgeMs = ground?.let { ageMs(nowMs, it.timestampMs) } ?: 0L,
                scanReason = s.scanUnknownReason(),
                groundSource = ground?.source ?: GroundSource.UNKNOWN,
                sameSession = s.evidenceBelongsToSession(active)
            )
        }
    }
}

object NeedViewPlanner {

    /** 与 [ArrivalAuthority.maxEvidenceAgeMs] 同值：显示层和裁决层不能各说一套。 */
    const val MAX_TARGET_AGE_MS = 2000L
    const val MAX_GROUND_AGE_MS = 2000L

    /** 还没过期但已经在变旧：提示"别动，继续看着"，不催重扫。 */
    const val UNSTABLE_TARGET_AGE_MS = 1000L

    data class Plan(val view: NeedView, val reasons: List<ReasonCode>, val prompt: String)

    fun plan(s: EvidenceSnapshot): Plan {
        val reasons = ArrayList<ReasonCode>()
        if (!s.sameSession) reasons.add(ReasonCode.SESSION_MISMATCH)
        if (s.addressConflict) reasons.add(ReasonCode.ADDRESS_CONFLICT)
        if (s.landmarkConflict) reasons.add(ReasonCode.LANDMARK_CONFLICT)
        if (s.relationConflict) reasons.add(ReasonCode.RELATION_CONFLICT)
        if (s.neighborConflict) reasons.add(ReasonCode.NEIGHBOR_CONFLICT)
        if (!s.targetLocked) {
            reasons.add(if (s.targetEverLocked) ReasonCode.TARGET_LOST else ReasonCode.TARGET_NOT_FOUND)
        } else {
            if (s.targetAgeMs > UNSTABLE_TARGET_AGE_MS && s.targetAgeMs <= MAX_TARGET_AGE_MS) {
                reasons.add(ReasonCode.TARGET_UNSTABLE)
            }
            if (s.targetAgeMs > MAX_TARGET_AGE_MS) reasons.add(ReasonCode.EVIDENCE_STALE)
        }
        if (s.targetLocked && s.doorCandidates == 0) reasons.add(ReasonCode.DOOR_NOT_FOUND)
        if (s.doorCandidates >= 2 && s.relationConfirmed != true) reasons.add(ReasonCode.MULTIPLE_DOORS)
        if (s.hasActiveDoor && s.relationConfirmed == null) reasons.add(ReasonCode.RELATION_UNKNOWN)
        // 已经被判成"隔壁"的那一扇不能算证据齐备：
        // 它是反向结论，不是"关系已确认所以可以往下走"。漏了这一条，
        // 界面会在关系冲突的同时念出"入口已确认"——这是最容易要人命的一句话。
        if (s.hasActiveDoor && s.relationConfirmed == false &&
            !reasons.contains(ReasonCode.RELATION_CONFLICT)) reasons.add(ReasonCode.RELATION_CONFLICT)
        when (s.ground) {
            GroundState.NOT_OBSERVED -> if (s.relationConfirmed == true) reasons.add(ReasonCode.GROUND_NOT_VISIBLE)
            GroundState.UNKNOWN -> reasons.add(ReasonCode.GROUND_UNKNOWN)
            GroundState.BLOCKED -> reasons.add(ReasonCode.GROUND_OBSTRUCTED)
            GroundState.OBSERVED_MANUAL, GroundState.OBSERVED_PROVISIONAL, GroundState.CLEAR ->
                if (s.groundAgeMs > MAX_GROUND_AGE_MS) reasons.add(ReasonCode.GROUND_STALE)
        }

        // 视图选择：第一个成立的冲突/缺口决定"下一步看哪里"
        val view = when {
            !s.sameSession -> NeedView.REOBSERVE_TARGET
            s.addressConflict || s.landmarkConflict || s.relationConflict -> NeedView.REOBSERVE_TARGET
            !s.targetLocked -> when (s.scan) {
                ScanDirection.LEFT -> NeedView.SCAN_LEFT
                ScanDirection.RIGHT -> NeedView.SCAN_RIGHT
                ScanDirection.FRONT -> NeedView.LOOK_UP_SIGN
                ScanDirection.UNKNOWN -> NeedView.SCAN_WIDER
            }
            s.doorCandidates == 0 -> NeedView.LOOK_AT_DOOR
            s.doorCandidates >= 2 && s.relationConfirmed != true -> NeedView.SCAN_WIDER
            // 眼前只有一扇、但它已经被判成隔壁：要的是"重新看招牌和门的关系"，
            // 绝不能落到最后的 NONE（那句话是"入口已确认"）。
            s.relationConfirmed == false -> NeedView.REOBSERVE_TARGET
            s.relationConfirmed == null -> NeedView.LOOK_AT_DOOR
            s.ground == GroundState.NOT_OBSERVED || s.ground == GroundState.UNKNOWN ||
                s.ground == GroundState.BLOCKED || s.groundAgeMs > MAX_GROUND_AGE_MS ->
                NeedView.LOOK_DOWN_GROUND
            else -> NeedView.NONE
        }
        return Plan(view, reasons.ifEmpty { listOf(ReasonCode.NONE) }, promptFor(view, s))
    }

    /** 短句；只说看到什么、下一步看哪里，不承诺安全，不给距离。 */
    fun promptFor(view: NeedView, s: EvidenceSnapshot): String = when (view) {
        NeedView.REOBSERVE_TARGET -> "证据有冲突，请重新扫一下招牌和门"
        NeedView.SCAN_LEFT -> "目标大概在左侧，请缓慢向左扫描"
        NeedView.SCAN_RIGHT -> "目标大概在右侧，请缓慢向右扫描"
        NeedView.SCAN_WIDER -> if (s.doorCandidates >= 2)
            "发现多个入口，请缓慢左右扫一下" else "方向暂时不可靠，请缓慢左右扫描"
        NeedView.LOOK_UP_SIGN -> "请站定，把镜头抬高一点找招牌"
        NeedView.LOOK_AT_DOOR -> "看到目标招牌，请顺着招牌下方找入口"
        NeedView.LOOK_DOWN_GROUND -> "入口可能已找到，请稍微向下拍摄门前区域"
        NeedView.HOLD_STILL -> "请保持不动"
        NeedView.NONE -> "入口已确认"
    }

    /** 调试面板用：把 reason code 翻成人话，语音只念最靠前的一个。 */
    fun describe(r: ReasonCode): String = when (r) {
        ReasonCode.NONE -> "证据齐备"
        ReasonCode.TARGET_NOT_FOUND -> "还没认出目标招牌"
        ReasonCode.TARGET_LOST -> "目标招牌已离开画面"
        ReasonCode.TARGET_UNSTABLE -> "目标还在画面里但已经不稳，请继续对准"
        ReasonCode.EVIDENCE_STALE -> "证据已过期"
        ReasonCode.DOOR_NOT_FOUND -> "还没看到入口候选"
        ReasonCode.MULTIPLE_DOORS -> "有多个入口，分不清哪一扇"
        ReasonCode.RELATION_UNKNOWN -> "还不能确认这扇属于目标"
        ReasonCode.NEIGHBOR_CONFLICT -> "旁边有别的门"
        ReasonCode.RELATION_CONFLICT -> "已判定这扇不属于目标"
        ReasonCode.ADDRESS_CONFLICT -> "门牌/楼号与目标不一致"
        ReasonCode.LANDMARK_CONFLICT -> "看到与目标矛盾的地标"
        ReasonCode.GROUND_NOT_VISIBLE -> "门前地面没进画面"
        ReasonCode.GROUND_UNKNOWN -> "门前地面看不清"
        ReasonCode.GROUND_STALE -> "门前地面证据已过期，需重新看一次"
        ReasonCode.GROUND_OBSTRUCTED -> "门前有阻挡"
        ReasonCode.SESSION_MISMATCH -> "证据来自另一个会话，已作废"
    }

    /** 面板用：方向为什么是 UNKNOWN。这一项专门防止"ScanCoach 坏了"的误判。 */
    fun describe(r: ScanUnknownReason): String = when (r) {
        ScanUnknownReason.NONE -> "-"
        ScanUnknownReason.ROUTE_BEARING_MISSING -> "ROUTE_BEARING_MISSING（目标方位未接入，非 ScanCoach 故障）"
        ScanUnknownReason.HEADING_MISSING -> "HEADING_MISSING（相机朝向不可用）"
        ScanUnknownReason.PHONE_NOT_UPRIGHT -> "PHONE_NOT_UPRIGHT（手机没拿正）"
        ScanUnknownReason.SENSOR_VALUE_INVALID -> "SENSOR_VALUE_INVALID（传感器数值异常）"
    }
}
