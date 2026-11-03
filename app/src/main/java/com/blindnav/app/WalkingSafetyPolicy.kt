package com.blindnav.app

/**
 * One corridor decision shared by path alignment and obstacle occupancy.
 *
 * Safety rules:
 *  - a single detector frame never stops;
 *  - near-focus alone never stops;
 *  - an obstacle must overlap the corridor and persist;
 *  - path side changes need dwell, more dwell when the phone was rotated.
 */
class WalkingSafetyPolicy(
    private val stopPersistMs: Long = 300L,
    private val stopHoldMs: Long = 800L,
    private val nearWarnMs: Long = 1_200L,
    private val sideFrames: Int = 3,
    private val sideFramesRotated: Int = 4,
    private val sideDwellMs: Long = 400L,
    private val sideDwellRotatedMs: Long = 700L,
    private val centerFrames: Int = 2,
    private val corridorLostMs: Long = 500L,
) {
    data class Hit(
        val name: String,
        val band: String,
        val corridorOverlap: Float,
        val bottom: Float,
    )

    data class Input(
        val nowMs: Long,
        val corridorAvailable: Boolean,
        val corridorSide: String,
        val corridorCurve: String,
        val corridorScore: Float,
        val hits: List<Hit>,
        val nearFocus: Boolean,
        val nearFocusSinceMs: Long,
        val walking: Boolean,
        val rotationSuspected: Boolean,
    )

    data class Decision(
        val corridorState: String,
        val risk: String,
        val pathCommand: String,
        val stopReason: String,
        val warnReason: String,
        val stableFrames: Int,
    )

    private var stopSince = 0L
    private var stopStreak = 0
    private var lastStopAt = 0L
    private var lastStop = false

    private var sideKey = ""
    private var sideStreak = 0
    private var sideSince = 0L
    private var centerStreak = 0
    private var lastCommand = "KEEP"
    private var corridorLostSince = 0L

    fun reset() {
        stopSince = 0L; stopStreak = 0; lastStopAt = 0L; lastStop = false
        sideKey = ""; sideStreak = 0; sideSince = 0L; centerStreak = 0
        lastCommand = "KEEP"; corridorLostSince = 0L
    }

    fun evaluate(input: Input, updateRisk: Boolean = true): Decision {
        val now = input.nowMs
        val available = input.corridorAvailable &&
            input.corridorSide in setOf("left", "center", "right") &&
            input.corridorScore.isFinite() && input.corridorScore >= 0.6f

        if (available) corridorLostSince = 0L
        else if (corridorLostSince == 0L) corridorLostSince = now
        val corridorState = when {
            available -> "AVAILABLE"
            now - corridorLostSince < corridorLostMs -> "DEGRADED"
            else -> "UNAVAILABLE"
        }

        val inCorridor = input.hits.filter { it.corridorOverlap >= 0.25f }
        if (updateRisk) {
            if (inCorridor.isNotEmpty()) {
                if (stopSince == 0L) stopSince = now
                stopStreak++
                lastStopAt = now
            } else {
                stopSince = 0L
                stopStreak = 0
            }
            val fresh = inCorridor.isNotEmpty() && stopStreak >= 2 && now - stopSince >= stopPersistMs
            val hold = lastStopAt > 0L && now - lastStopAt < stopHoldMs
            lastStop = fresh || (hold && lastStop)
        }

        val nearPersist = input.nearFocus && input.nearFocusSinceMs > 0L &&
            now >= input.nearFocusSinceMs && now - input.nearFocusSinceMs >= nearWarnMs
        val warn = !lastStop && nearPersist && input.walking && available

        val risk = when {
            lastStop -> "STOP"
            warn -> "WARN"
            else -> "CLEAR"
        }

        var stable = 0
        val command = when {
            lastStop -> {
                sideKey = ""; sideStreak = 0; centerStreak = 0
                "STOP"
            }
            !available -> {
                sideKey = ""; sideStreak = 0; centerStreak = 0
                if (corridorState == "UNAVAILABLE") "REOBSERVE" else lastCommand
            }
            input.corridorSide == "center" -> {
                centerStreak++
                sideStreak = 0
                sideKey = ""
                stable = centerStreak
                if (centerStreak >= centerFrames) lastCommand = "KEEP"
                lastCommand
            }
            else -> {
                centerStreak = 0
                val side = input.corridorSide
                if (side != sideKey) {
                    sideKey = side
                    sideStreak = 1
                    sideSince = now
                } else sideStreak++
                stable = sideStreak
                val needFrames = if (input.rotationSuspected) sideFramesRotated else sideFrames
                val needMs = if (input.rotationSuspected) sideDwellRotatedMs else sideDwellMs
                if (sideStreak >= needFrames && now - sideSince >= needMs) {
                    lastCommand = if (side == "left") "LEFT" else "RIGHT"
                }
                lastCommand
            }
        }

        val stopReason = when {
            lastStop && inCorridor.isEmpty() -> "stop_hold"
            lastStop -> "corridor_obstacle_persistent"
            else -> ""
        }
        val warnReason = if (warn) "near_focus_walking_corridor" else ""
        return Decision(corridorState, risk, command, stopReason, warnReason, stable)
    }
}
