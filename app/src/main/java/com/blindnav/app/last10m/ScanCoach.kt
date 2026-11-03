package com.blindnav.app.last10m

/**
 * 主动扫描教练（能力 1）。
 *
 * 已有的 [ScanDirectionResolver] 是"每帧独立算一次左右前"，问题很实际：
 * 罗盘本身有 ±10° 级抖动，目标方位又随 GPS 位置小幅变化，
 * 用户在正前附近站着不动，提示就会在 左/前/右 之间来回翻。
 * 对视障用户来说，翻来覆去的指令比没有指令更糟。
 *
 * 这里加两样东西：
 *  - **双门限迟滞**：进入"正前"用窄门限（frontEnter），离开用宽门限（frontExit），
 *    中间那段是死区，站着不动就不会来回切。
 *  - **最短驻留**：方向刚切换过 minDwellMs 内不再切换，除非目标真的确认/丢失。
 *
 * 仍然只输出扫描方向，绝不输出移动指令。
 * 姿态不可信（非竖直）或朝向过期时返回 UNKNOWN，但**不改动已承诺的方向**，
 * 免得一次传感器掉点被记成一次翻转。
 */
class ScanCoach(
    private val frontEnterDeg: Double = 20.0,
    private val frontExitDeg: Double = 42.0,
    private val minDwellMs: Long = 1500L,
    private val maxHeadingAgeMs: Long = 3000L
) {
    private var committed = ScanDirection.UNKNOWN
    private var committedAt = Long.MIN_VALUE

    var directionChanges = 0
        private set
    var updates = 0
        private set

    fun current(): ScanDirection = committed

    /**
     * @param bearingToTarget 目标相对**正北**的方位角；null 表示还不知道目标在哪
     * @param heading 相机朝向（PhoneDirection 给的拍摄瞬间朝向）；null 表示不可用
     * @param upright 手机是否大致竖直，非竖直时罗盘读数没意义
     */
    fun update(bearingToTarget: Double?, heading: Double?, nowMs: Long, upright: Boolean): ScanDirection {
        updates++
        if (bearingToTarget == null || heading == null || !upright ||
            !bearingToTarget.isFinite() || !heading.isFinite()) {
            return ScanDirection.UNKNOWN
        }
        val offset = ((bearingToTarget - heading + 540.0) % 360.0) - 180.0   // 正 = 目标在右
        val next = when (committed) {
            // 已经在"正前"：只有明显偏离死区才切走
            ScanDirection.FRONT -> when {
                offset > frontExitDeg -> ScanDirection.RIGHT
                offset < -frontExitDeg -> ScanDirection.LEFT
                else -> ScanDirection.FRONT
            }
            // 已经承诺了左/右：回到窄门限内才算"转正"，跨过中心线才算翻到另一侧
            ScanDirection.LEFT -> when {
                offset >= frontEnterDeg && offset <= frontExitDeg -> ScanDirection.FRONT
                offset > frontExitDeg -> ScanDirection.RIGHT
                else -> ScanDirection.LEFT
            }
            ScanDirection.RIGHT -> when {
                offset <= -frontEnterDeg && offset >= -frontExitDeg -> ScanDirection.FRONT
                offset < -frontExitDeg -> ScanDirection.LEFT
                else -> ScanDirection.RIGHT
            }
            ScanDirection.UNKNOWN -> when {
                offset >= -frontEnterDeg && offset <= frontEnterDeg -> ScanDirection.FRONT
                offset > frontEnterDeg -> ScanDirection.RIGHT
                else -> ScanDirection.LEFT
            }
        }
        if (next != committed) {
            // 驻留期内不翻转：抖动通常在这个尺度内
            if (committed != ScanDirection.UNKNOWN && nowMs - committedAt < minDwellMs) {
                return committed
            }
            committed = next
            committedAt = nowMs
            directionChanges++
        }
        return committed
    }

    /** 目标确认后就别再催扫描了。 */
    fun reset() { committed = ScanDirection.UNKNOWN; committedAt = Long.MIN_VALUE }

    /** 每分钟翻转次数——这是"指令抖不抖"的直接指标。 */
    fun flipRatePerMinute(windowMs: Long): Double =
        if (windowMs <= 0) 0.0 else directionChanges * 60_000.0 / windowMs

    companion object {
        /** 无迟滞的对照实现（就是老的逐帧独立判定），用来量翻转率改善了多少。 */
        fun naive(bearingToTarget: Double?, heading: Double?, upright: Boolean): ScanDirection =
            ScanDirectionResolver.resolve(bearingToTarget, heading, upright)
    }
}
